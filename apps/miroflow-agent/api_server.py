# Copyright (c) 2025 MiroMind
# This source code is licensed under the MIT License.

import asyncio
import hashlib
import json
import logging
import os
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional

import hydra
from fastapi import FastAPI, Header, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from omegaconf import DictConfig, OmegaConf
from pydantic import BaseModel

from src.core.pipeline import create_pipeline_components, execute_task_pipeline
from src.logging.task_logger import bootstrap_logger

# Configure logger
logger = bootstrap_logger()

# Global configuration storage
_cfg: Optional[DictConfig] = None
_default_cfg: Optional[DictConfig] = None  # Store CLI-provided default config
_sessions: Dict[str, Dict] = {}


class SessionAwareSandboxManager:
    """
    Wrapper for ToolManager that automatically manages sandbox creation and reuse per session.
    
    This ensures that all Python tool calls within the same session use the same sandbox,
    without requiring the agent to explicitly pass sandbox_id or manage sandbox lifecycle.
    """
    
    def __init__(self, tool_manager, session_dict: Dict):
        """
        Initialize the SessionAwareSandboxManager.
        
        Args:
            tool_manager: The underlying ToolManager instance
            session_dict: Reference to the session dictionary where sandbox_id is stored
        """
        self.tool_manager = tool_manager
        self.session_dict = session_dict
        self._sandbox_creation_lock = asyncio.Lock()
    
    async def _ensure_sandbox_exists(self) -> str:
        """
        Ensure a sandbox exists for this session, creating one if necessary.
        
        Returns:
            The sandbox_id for this session
        """
        async with self._sandbox_creation_lock:
            sandbox_id = self.session_dict.get("sandbox_id")
            
            # If sandbox_id exists, trust it and return directly
            # If it's actually expired/unavailable, the tool call will fail
            # and we'll handle that in execute_tool_call
            if sandbox_id:
                logger.info(f"Reusing existing sandbox {sandbox_id}")
                return sandbox_id
            
            # Create a new sandbox
            logger.info("Creating new sandbox for session")
            result = await self.tool_manager.execute_tool_call(
                server_name="tool-python",
                tool_name="create_sandbox",
                arguments={"timeout": 3600}  # 1 hour TTL
            )
            
            if "result" in result and "sandbox_id:" in result["result"]:
                sandbox_id = result["result"].split("sandbox_id:")[-1].strip()
                self.session_dict["sandbox_id"] = sandbox_id
                logger.info(f"Created new sandbox {sandbox_id} for session")
                return sandbox_id
            else:
                # Check for error field for more robust error detection
                error_msg = result.get("error", str(result))
                raise Exception(f"Failed to create sandbox: {error_msg}")
    
    def _needs_sandbox_id(self, tool_name: str, arguments: dict) -> bool:
        """
        Check if a tool call needs a sandbox_id parameter.
        
        Args:
            tool_name: The name of the tool being called
            arguments: The arguments for the tool call
            
        Returns:
            True if this tool needs sandbox_id and it's not already provided
        """
        # Tools that require sandbox_id
        sandbox_tools = {
            "run_command",
            "run_python_code", 
            "run_python_code_stream",
            "upload_file_from_local_to_sandbox",
            "download_file_from_sandbox_to_local",
            "download_file_from_internet_to_sandbox"
        }
        
        # Check if this tool needs sandbox_id and doesn't already have it
        return tool_name in sandbox_tools and "sandbox_id" not in arguments
    
    async def execute_tool_call(self, server_name: str, tool_name: str, arguments: dict):
        """
        Execute a tool call, automatically injecting sandbox_id for Python tools.
        
        Args:
            server_name: The name of the MCP server
            tool_name: The name of the tool to call
            arguments: The arguments for the tool
            
        Returns:
            The result of the tool call
        """
        # If this is a Python tool that needs a sandbox_id, inject it
        if server_name == "tool-python" and self._needs_sandbox_id(tool_name, arguments):
            try:
                sandbox_id = await self._ensure_sandbox_exists()
                # Inject sandbox_id into arguments
                arguments = {**arguments, "sandbox_id": sandbox_id}
                logger.info(f"Auto-injected sandbox_id {sandbox_id} for tool {tool_name}")
            except Exception as e:
                logger.error(f"Failed to ensure sandbox exists: {e}", exc_info=True)
                # Let the tool call proceed without sandbox_id, it will fail with appropriate error
        
        # Delegate to the underlying tool manager
        result = await self.tool_manager.execute_tool_call(
            server_name=server_name,
            tool_name=tool_name,
            arguments=arguments
        )
        
        # If sandbox-related tool call failed due to sandbox unavailability, recreate and retry once
        if (
            server_name == "tool-python"
            and self._needs_sandbox_id(tool_name, arguments)
            and self._is_sandbox_error(result)
        ):
            # Protect sandbox reset/creation with the sandbox creation lock to avoid races
            async with self._sandbox_creation_lock:
                sandbox_id = self.session_dict.get("sandbox_id")
                current_sandbox_id = self.session_dict.get("sandbox_id")
                
                # Check if another task has already refreshed the sandbox
                if current_sandbox_id and current_sandbox_id != sandbox_id:
                    logger.info(
                        f"Sandbox error detected, but sandbox_id was already updated "
                        f"to {current_sandbox_id}; skipping sandbox recreation in this call."
                    )
                    return result
                
                if sandbox_id:
                    logger.warning(f"Sandbox {sandbox_id} is unavailable, recreating and retrying...")
                else:
                    logger.warning(
                        "Sandbox is unavailable or was not initialized, creating new sandbox and retrying..."
                    )
                
                # Clear the cached sandbox_id so a fresh one is created
                self.session_dict["sandbox_id"] = None
                
                # Retry with new sandbox
                try:
                    new_sandbox_id = await self._ensure_sandbox_exists()
                    arguments = {**arguments, "sandbox_id": new_sandbox_id}
                    logger.info(f"Retrying with new sandbox {new_sandbox_id}")
                    result = await self.tool_manager.execute_tool_call(
                        server_name=server_name,
                        tool_name=tool_name,
                        arguments=arguments
                    )
                except Exception as e:
                    logger.error(f"Failed to recreate sandbox and retry: {e}", exc_info=True)
        
        return result
    
    def _is_sandbox_error(self, result: dict) -> bool:
        """
        Check if a tool call result indicates a sandbox connectivity error.
        
        Args:
            result: The result from a tool call
            
        Returns:
            True if this is a sandbox unavailability error
        """
        # Check for error field
        if "error" in result:
            error_msg = str(result["error"]).lower()
            return any(phrase in error_msg for phrase in [
                "failed to connect to sandbox",
                "sandbox does not exist",
                "sandbox not found",
                "connection refused"
            ])
        
        # Check for [ERROR] in result field (format used by Python MCP server)
        if "result" in result:
            result_str = str(result["result"])
            if "[ERROR]" in result_str:
                result_lower = result_str.lower()
                return any(phrase in result_lower for phrase in [
                    "failed to connect to sandbox",
                    "sandbox does not exist",
                    "sandbox not found"
                ])
        
        return False
    
    def __getattr__(self, name):
        """
        Delegate all other attribute access to the underlying tool_manager.
        """
        return getattr(self.tool_manager, name)


def _create_session_with_wrapped_managers(cfg: DictConfig) -> Dict:
    """
    Create a new session with tool managers wrapped in SessionAwareSandboxManager.
    
    Args:
        cfg: Hydra configuration for the session
        
    Returns:
        Session dictionary with wrapped tool managers
    """
    main_agent_tool_manager, sub_agent_tool_managers, output_formatter = (
        create_pipeline_components(cfg)
    )
    
    # Automatically blacklist create_sandbox tool since SessionAwareSandboxManager
    # handles sandbox creation automatically. This prevents the LLM from directly
    # calling create_sandbox and overwriting the session's sandbox.
    main_agent_tool_manager.tool_blacklist.add(("tool-python", "create_sandbox"))
    logger.info("Auto-blacklisted 'create_sandbox' tool - sandbox management is automatic")
    
    for sub_agent_tool_manager in sub_agent_tool_managers.values():
        sub_agent_tool_manager.tool_blacklist.add(("tool-python", "create_sandbox"))
    
    # Create session dict
    session_dict = {
        "cfg": cfg,
        "sandbox_id": None,  # Will be created on first use
        "output_formatter": output_formatter,
    }
    
    # Wrap the main agent tool manager with SessionAwareSandboxManager
    # This automatically manages sandbox creation and reuse
    session_dict["main_agent_tool_manager"] = SessionAwareSandboxManager(
        main_agent_tool_manager, session_dict
    )
    
    # Wrap sub-agent tool managers as well
    wrapped_sub_agents = {}
    for agent_name, sub_agent_tool_manager in sub_agent_tool_managers.items():
        wrapped_sub_agents[agent_name] = SessionAwareSandboxManager(
            sub_agent_tool_manager, session_dict
        )
    session_dict["sub_agent_tool_managers"] = wrapped_sub_agents
    
    return session_dict


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events"""
    # Startup
    logger.info("Starting MiroFlow Agent API Server")
    initialize_config()
    yield
    # Shutdown
    logger.info("Shutting down MiroFlow Agent API Server")
    # Clean up all sessions
    for session_id, session in _sessions.items():
        # Close tool managers if needed
        pass


app = FastAPI(title="MiroFlow Agent API", lifespan=lifespan)


class QueryRequest(BaseModel):
    query: str
    history: Optional[List[Dict[str, str]]] = None
    is_confirmed: bool
    # Optional Hydra configuration overrides
    config_overrides: Optional[Dict[str, str]] = None


def initialize_config(overrides: Optional[List[str]] = None):
    """
    Initialize Hydra configuration with optional overrides.
    
    Args:
        overrides: List of Hydra override strings (e.g., ["llm=qwen-3", "llm.base_url=http://localhost:8000"])
    
    Returns:
        DictConfig: The composed configuration
    """
    global _cfg, _default_cfg
    
    # If we have a CLI-provided default config, use it as base
    if _default_cfg is not None:
        if overrides:
            # Apply additional overrides on top of CLI config using OmegaConf.merge
            # This preserves CLI settings and applies request-specific overrides
            merged_cfg = OmegaConf.create(_default_cfg)
            for override in overrides:
                # Parse override string (e.g., "llm.temperature=0.7")
                key, value = override.split("=", 1)
                OmegaConf.update(merged_cfg, key, value, merge=True)
            return merged_cfg
        return _default_cfg
    
    # Otherwise, initialize with default config
    if _cfg is None:
        # Initialize Hydra with default config (only once at startup)
        with hydra.initialize(config_path="conf", version_base=None):
            _cfg = hydra.compose(config_name="config")
    
    # If overrides are provided, create a new config with those overrides
    if overrides:
        with hydra.initialize(config_path="conf", version_base=None):
            return hydra.compose(config_name="config", overrides=overrides)
    
    return _cfg


async def stream_generator(
    query: str,
    history: Optional[List[Dict[str, str]]],
    is_confirmed: bool,
    session_id: str,
    config_overrides: Optional[Dict[str, str]] = None,
):
    """
    Generate streaming responses in NDJSON format.
    
    Transforms internal streaming events to the required format:
    - {"status":"plan", "step": 1, "data":"..."}
    - {"status":"answer", "data":"..."}
    
    Args:
        query: User query
        history: Conversation history (currently not used)
        is_confirmed: Whether plan is confirmed
        session_id: Session identifier
        config_overrides: Optional Hydra config overrides (e.g., {"llm.provider": "qwen", "llm.base_url": "http://..."})
    """
    # Create async queue for receiving streaming updates
    stream_queue = asyncio.Queue()
    
    # Convert config_overrides dict to Hydra override list format
    override_list = None
    if config_overrides:
        override_list = [f"{key}={value}" for key, value in config_overrides.items()]
        logger.info(f"Config overrides for session {session_id}: {override_list}")
    
    # Get or create pipeline components for this session
    # Note: If config overrides are provided, we create new components even if session exists
    session_key = session_id
    if config_overrides:
        # Create a unique session key that includes config overrides
        # This ensures different configs create different sessions
        # Use deterministic hash for consistent session keys across restarts
        config_str = json.dumps(config_overrides, sort_keys=True)
        config_hash = hashlib.md5(config_str.encode()).hexdigest()[:8]
        session_key = f"{session_id}_{config_hash}"
    
    if session_key not in _sessions:
        cfg = initialize_config(overrides=override_list)
        _sessions[session_key] = _create_session_with_wrapped_managers(cfg)
    
    session = _sessions[session_key]
    cfg = session["cfg"]
    
    # Prepare task parameters
    task_id = f"api_{session_id}"
    task_description = query
    task_file_name = ""
    
    # Track current step for plan events
    current_step = 0
    in_plan_phase = not is_confirmed
    
    async def consume_stream():
        """Consume stream events and transform them"""
        nonlocal current_step
        
        try:
            while True:
                event = await stream_queue.get()
                if event is None:  # End of stream signal
                    break
                
                event_type = event.get("event")
                data = event.get("data", {})
                
                # Transform events to NDJSON format
                if event_type == "tool_call":
                    tool_name = data.get("tool_name", "")
                    tool_input = data.get("tool_input", data.get("delta_input", {}))
                    
                    # show_text tool is used to display answers, but we skip it during streaming
                    # because the content is already being streamed via "message" events
                    # This prevents duplication of content
                    if tool_name == "show_text":
                        # Skip show_text during streaming to avoid duplication
                        pass
                    
                    # show_error is for errors, also treat as answer
                    elif tool_name == "show_error":
                        error_text = tool_input.get("error", "")
                        if error_text:
                            output = {
                                "status": "answer",
                                "data": f"Error: {error_text}"
                            }
                            yield json.dumps(output, ensure_ascii=False) + "\n"
                    
                    # Other tool calls are planning steps
                    else:
                        if in_plan_phase:
                            current_step += 1
                            plan_text = f"Using tool: {tool_name}"
                            if tool_input:
                                # Truncate large inputs for display
                                input_str = json.dumps(tool_input, ensure_ascii=False)
                                if len(input_str) > 200:
                                    input_str = input_str[:200] + "..."
                                plan_text += f" with input: {input_str}"
                            
                            output = {
                                "status": "plan",
                                "step": current_step,
                                "data": plan_text
                            }
                            yield json.dumps(output, ensure_ascii=False) + "\n"
                
                elif event_type == "message":
                    # Messages are assistant responses (answer phase)
                    delta_content = data.get("delta", {}).get("content", "")
                    if delta_content:
                        # Send content immediately for real-time streaming
                        # No buffering - stream each token/chunk as it arrives from LLM
                        output = {
                            "status": "answer",
                            "data": delta_content
                        }
                        yield json.dumps(output, ensure_ascii=False) + "\n"
                
                elif event_type == "start_of_agent":
                    # Starting an agent indicates planning
                    if in_plan_phase:
                        current_step += 1
                        agent_name = data.get("display_name", data.get("agent_name", "agent"))
                        output = {
                            "status": "plan",
                            "step": current_step,
                            "data": f"Starting agent: {agent_name}"
                        }
                        yield json.dumps(output, ensure_ascii=False) + "\n"
                
                elif event_type == "start_of_workflow":
                    # Workflow start
                    if in_plan_phase:
                        current_step += 1
                        output = {
                            "status": "plan",
                            "step": current_step,
                            "data": "Workflow started"
                        }
                        yield json.dumps(output, ensure_ascii=False) + "\n"
                
                elif event_type == "end_of_workflow":
                    # Workflow end - signal completion
                    break
                
        except Exception as e:
            logger.error(f"Error in stream consumer: {e}", exc_info=True)
            error_output = {
                "status": "answer",
                "data": f"Error: {str(e)}"
            }
            yield json.dumps(error_output, ensure_ascii=False) + "\n"
    
    # Start pipeline execution in background
    async def run_pipeline():
        try:
            await execute_task_pipeline(
                cfg=cfg,
                task_id=task_id,
                task_file_name=task_file_name,
                task_description=task_description,
                main_agent_tool_manager=session["main_agent_tool_manager"],
                sub_agent_tool_managers=session["sub_agent_tool_managers"],
                output_formatter=session["output_formatter"],
                log_dir=cfg.debug_dir,
                stream_queue=stream_queue,
            )
        except Exception as e:
            logger.error(f"Error in pipeline execution: {e}", exc_info=True)
        finally:
            # Signal end of stream
            await stream_queue.put(None)
    
    # Start pipeline in background
    asyncio.create_task(run_pipeline())
    
    # Yield transformed stream events
    async for output in consume_stream():
        yield output


@app.post("/get_response")
async def get_response(
    request: QueryRequest,
    x_session_id: str = Header(..., alias="X-Session-Id"),
):
    """
    Submit a question and stream Scheduler's full output.
    
    Args:
        request: Request body containing query, history, is_confirmed, and optional config_overrides
        x_session_id: Session ID from header
    
    Returns:
        StreamingResponse with NDJSON format:
        - {"status":"plan", "step": 1, "data":"..."}
        - {"status":"answer", "data":"..."}
    
    Note:
        The 'history' parameter is currently not used by the underlying
        orchestrator implementation. Each request starts a new conversation.
        Future versions may support conversation history.
        
        The 'config_overrides' parameter allows overriding Hydra configuration
        settings, such as LLM provider, model name, base_url, etc.
        Example: {"llm.provider": "qwen", "llm.base_url": "http://localhost:8000/v1"}
    """
    try:
        logger.info(f"Received request for session {x_session_id}: {request.query}")
        
        # Note: history parameter is accepted but not currently used
        # The orchestrator initializes its own message history
        if request.history:
            logger.warning(
                f"History parameter provided but not currently supported. "
                f"Starting new conversation for session {x_session_id}"
            )
        
        # Log config overrides if provided
        if request.config_overrides:
            logger.info(f"Config overrides provided: {request.config_overrides}")
        
        return StreamingResponse(
            stream_generator(
                query=request.query,
                history=request.history,
                is_confirmed=request.is_confirmed,
                session_id=x_session_id,
                config_overrides=request.config_overrides,
            ),
            media_type="application/x-ndjson",
        )
    
    except Exception as e:
        logger.error(f"Error processing request: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}


@app.post("/upload_file")
async def upload_file(
    file: UploadFile = File(...),
    x_session_id: str = Header(..., alias="X-Session-Id"),
):
    """
    Upload a file directly to the E2B sandbox for the given session.
    
    Args:
        file: The file to upload (from multipart/form-data)
        x_session_id: Session ID from header
    
    Returns:
        JSON response with the file path in the sandbox
        Example: {"data": {"path": "/home/user/example.txt", "sandbox_id": "abc123"}}
    
    Note:
        - Each X-Session-Id corresponds to a session with a sandbox
        - Files are uploaded directly to the E2B sandbox, not the API server
        - Sandbox lifecycle is 3600 seconds (default TTL)
        - The server is stateless and does not maintain conversation history
    """
    try:
        # Validate filename
        if not file.filename or file.filename.strip() == "":
            raise HTTPException(status_code=400, detail="Filename is required")
        
        # Sanitize filename to prevent path traversal attacks
        safe_filename = os.path.basename(file.filename)
        
        # Additional validation
        if ".." in safe_filename or "/" in safe_filename or "\\" in safe_filename:
            raise HTTPException(
                status_code=400, 
                detail="Invalid filename: path traversal characters not allowed"
            )
        
        logger.info(f"Received file upload request for session {x_session_id}: {safe_filename}")
        
        # Get or create session
        session_key = x_session_id
        if session_key not in _sessions:
            # Initialize session with default config
            cfg = initialize_config()
            _sessions[session_key] = _create_session_with_wrapped_managers(cfg)
        
        session = _sessions[session_key]
        tool_manager = session["main_agent_tool_manager"]
        
        # The SessionAwareSandboxManager will automatically create and inject sandbox_id
        # So we can directly call the upload tool without managing sandbox_id manually
        
        async def upload_to_sandbox(local_path: str):
            """Upload file to the session's sandbox (sandbox_id auto-injected)."""
            sandbox_file_path = f"/home/user/{safe_filename}"
            logger.info(f"Uploading {local_path} to session sandbox at {sandbox_file_path}")
            
            # Note: sandbox_id will be automatically injected by SessionAwareSandboxManager
            upload_result = await tool_manager.execute_tool_call(
                server_name="tool-python",
                tool_name="upload_file_from_local_to_sandbox",
                arguments={
                    # sandbox_id is auto-injected, no need to pass it explicitly
                    "local_file_path": local_path,
                    "sandbox_file_path": "/home/user"
                }
            )
            if "result" in upload_result:
                result_str = upload_result["result"]
                if "[ERROR]" in result_str:
                    raise Exception(result_str)
                logger.info(f"File uploaded successfully to sandbox: {result_str}")
                
                # Rename the uploaded file to the correct filename
                # The uploaded file has the temporary filename, we need to rename it
                uploaded_temp_name = os.path.basename(local_path)
                if uploaded_temp_name != safe_filename:
                    logger.info(f"Renaming uploaded file from {uploaded_temp_name} to {safe_filename}")
                    # Note: sandbox_id is auto-injected by SessionAwareSandboxManager
                    rename_result = await tool_manager.execute_tool_call(
                        server_name="tool-python",
                        tool_name="run_command",
                        arguments={
                            # sandbox_id is auto-injected
                            "command": f"mv /home/user/{uploaded_temp_name} /home/user/{safe_filename}"
                        }
                    )
                    if "result" in rename_result:
                        rename_str = rename_result["result"]
                        if "[ERROR]" in rename_str:
                            logger.warning(f"Failed to rename file: {rename_str}")
                        else:
                            logger.info(f"File renamed successfully to {safe_filename}")
                
                return sandbox_file_path
            raise Exception(f"Upload failed: {upload_result}")
        
        # Save file to temporary location first
        with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{safe_filename}") as tmp_file:
            content = await file.read()
            tmp_file.write(content)
            tmp_file_path = tmp_file.name
        
        try:
            # SessionAwareSandboxManager handles sandbox creation and retry logic
            sandbox_file_path = await upload_to_sandbox(tmp_file_path)
            
            return {
                "data": {
                    "path": sandbox_file_path
                }
            }
        
        finally:
            # Clean up temporary file
            try:
                os.unlink(tmp_file_path)
            except Exception as e:
                logger.warning(f"Failed to delete temporary file {tmp_file_path}: {e}")
    
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logger.error(f"Error uploading file: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@hydra.main(config_path="conf", config_name="config", version_base=None)
def main(cfg: DictConfig) -> None:
    """
    Main entry point with Hydra configuration support.
    
    Allows overriding configuration from command line:
        python api_server.py llm=qwen-3 llm.api_key=xxxxx llm.base_url=http://localhost:8000/v1
    """
    global _default_cfg
    
    # Store the CLI-provided config as default
    _default_cfg = cfg
    
    logger.info("=" * 50)
    logger.info("MiroFlow Agent API Server - Configuration")
    logger.info("=" * 50)
    logger.info(OmegaConf.to_yaml(cfg))
    logger.info("=" * 50)
    
    import uvicorn
    
    # Run the FastAPI server
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
