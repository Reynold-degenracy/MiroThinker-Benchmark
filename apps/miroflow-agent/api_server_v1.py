# Copyright (c) 2025 MiroMind
# This source code is licensed under the MIT License.

import asyncio
import hashlib
import json
import logging
import os
import tempfile
from contextlib import asynccontextmanager
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

# Constants
DEFAULT_SANDBOX_TIMEOUT = 3600  # 1 hour TTL for sandboxes
TOOL_INPUT_TRUNCATE_LENGTH = 200  # Max length for displaying tool inputs
STREAMING_CHUNK_SIZE = 10  # Character chunk size for streaming effects
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000

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
                arguments={"timeout": DEFAULT_SANDBOX_TIMEOUT}
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
            True if this tool needs sandbox_id and doesn't have a valid one
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
        
        if tool_name not in sandbox_tools:
            return False
        
        # Check if sandbox_id is missing or invalid
        sandbox_id = arguments.get("sandbox_id")
        if not sandbox_id:
            return True
        
        # Invalid sandbox IDs that should be replaced (from python_mcp_server.py)
        INVALID_SANDBOX_IDS = {
            "default", "sandbox1", "sandbox", "some_id", "new_sandbox",
            "python", "create_sandbox", "sandbox123", "temp",
            "sandbox-0", "sandbox-1", "sandbox_0", "sandbox_1",
            "new", "0", "auto", "default_sandbox", "none",
            "sandbox_12345", "dummy", "sandbox_01",
        }
        
        # If the provided sandbox_id is invalid, we need to inject the real one
        if sandbox_id in INVALID_SANDBOX_IDS:
            logger.warning(f"Invalid sandbox_id '{sandbox_id}' detected, will replace with session sandbox")
            return True
        
        return False
    
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
            # Capture the sandbox_id before acquiring the lock to detect concurrent updates
            sandbox_id = self.session_dict.get("sandbox_id")
            
            # Protect sandbox reset/creation with the sandbox creation lock to avoid races
            async with self._sandbox_creation_lock:
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
    logger.info("Starting MiroFlow Agent API Server v1")
    initialize_config()
    yield
    # Shutdown
    logger.info("Shutting down MiroFlow Agent API Server v1")
    # Clean up all sessions
    for session_id, session in _sessions.items():
        # Close tool managers if needed
        pass


app = FastAPI(title="MiroFlow Agent API v1", lifespan=lifespan)


# Request/Response Models
class Message(BaseModel):
    type: str
    step: int = -1
    content: Optional[str] = None
    delta: Optional[str] = None


class PlanRequest(BaseModel):
    query: str
    history: Optional[List[Message]] = None


class ExecuteRequest(BaseModel):
    plan: List[Message]


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


async def plan_stream_generator(
    query: str,
    history: Optional[List[Message]],
    session_id: str,
):
    """
    Generate streaming plan responses in the v1 API format.
    
    Yields messages in format:
    - {"type": "start", "step": 1, "delta": ""}
    - {"type": "plan", "step": 1, "delta": "xxx"}
    - {"type": "end", "step": 1, "delta": ""}
    
    Args:
        query: User query
        history: Conversation history
        session_id: Session identifier
    """
    # Create async queue for receiving streaming updates
    stream_queue = asyncio.Queue()
    
    # Get or create pipeline components for this session
    session_key = session_id
    if session_key not in _sessions:
        cfg = initialize_config()
        _sessions[session_key] = _create_session_with_wrapped_managers(cfg)
    
    session = _sessions[session_key]
    cfg = session["cfg"]
    
    # Prepare task parameters
    task_id = f"api_v1_plan_{session_id}"
    task_description = query
    task_file_name = ""
    
    # Track current step for plan events
    current_step = 0
    step_buffer = []  # Buffer to accumulate text for each step
    
    async def consume_stream():
        """Consume stream events and transform them to v1 API format"""
        nonlocal current_step
        
        try:
            while True:
                event = await stream_queue.get()
                if event is None:  # End of stream signal
                    # Flush any remaining buffered step
                    if step_buffer:
                        for chunk in step_buffer:
                            yield json.dumps(chunk, ensure_ascii=False) + "\n"
                        # Send end event for last step
                        end_event = {"type": "end", "step": current_step, "delta": ""}
                        yield json.dumps(end_event, ensure_ascii=False) + "\n"
                    break
                
                event_type = event.get("event")
                data = event.get("data", {})
                
                # Transform events to v1 API format
                if event_type == "tool_call":
                    tool_name = data.get("tool_name", "")
                    tool_input = data.get("tool_input", data.get("delta_input", {}))
                    
                    # Skip show_text tool during planning
                    if tool_name == "show_text":
                        pass
                    else:
                        # Flush previous step if exists
                        if step_buffer:
                            for chunk in step_buffer:
                                yield json.dumps(chunk, ensure_ascii=False) + "\n"
                            # Send end event for previous step
                            end_event = {"type": "end", "step": current_step, "delta": ""}
                            yield json.dumps(end_event, ensure_ascii=False) + "\n"
                            step_buffer = []
                        
                        # Start new step
                        current_step += 1
                        
                        # Send start event
                        start_event = {"type": "start", "step": current_step, "delta": ""}
                        yield json.dumps(start_event, ensure_ascii=False) + "\n"
                        
                        # Send plan content in chunks
                        plan_text = f"Using tool: {tool_name}"
                        if tool_input:
                            input_str = json.dumps(tool_input, ensure_ascii=False)
                            if len(input_str) > TOOL_INPUT_TRUNCATE_LENGTH:
                                input_str = input_str[:TOOL_INPUT_TRUNCATE_LENGTH] + "..."
                            plan_text += f" with input: {input_str}"
                        
                        # Split into smaller chunks for streaming effect
                        for i in range(0, len(plan_text), STREAMING_CHUNK_SIZE):
                            chunk = plan_text[i:i + STREAMING_CHUNK_SIZE]
                            plan_event = {"type": "plan", "step": current_step, "delta": chunk}
                            step_buffer.append(plan_event)
                
                elif event_type == "start_of_workflow":
                    # Workflow start - create first step
                    current_step += 1
                    start_event = {"type": "start", "step": current_step, "delta": ""}
                    yield json.dumps(start_event, ensure_ascii=False) + "\n"
                    
                    plan_text = "Workflow started"
                    plan_event = {"type": "plan", "step": current_step, "delta": plan_text}
                    step_buffer.append(plan_event)
                
                elif event_type == "end_of_workflow":
                    # Workflow end - flush and exit
                    if step_buffer:
                        for chunk in step_buffer:
                            yield json.dumps(chunk, ensure_ascii=False) + "\n"
                        end_event = {"type": "end", "step": current_step, "delta": ""}
                        yield json.dumps(end_event, ensure_ascii=False) + "\n"
                    break
                
        except Exception as e:
            logger.error(f"Error in plan stream consumer: {e}", exc_info=True)
    
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


async def execute_stream_generator(
    plan: List[Message],
    session_id: str,
):
    """
    Generate streaming execution responses in the v1 API format.
    
    Yields messages in format:
    - {"type": "start", "step": 1, "delta": ""}
    - {"type": "answer", "step": 1, "delta": "xxx"}
    - {"type": "end", "step": 1, "delta": ""}
    
    Args:
        plan: List of plan messages
        session_id: Session identifier
    """
    # Create async queue for receiving streaming updates
    stream_queue = asyncio.Queue()
    
    # Get or create pipeline components for this session
    session_key = session_id
    if session_key not in _sessions:
        cfg = initialize_config()
        _sessions[session_key] = _create_session_with_wrapped_managers(cfg)
    
    session = _sessions[session_key]
    cfg = session["cfg"]
    
    # Construct task description from plan
    task_description = "\n".join([
        f"Step {msg.step}: {msg.content}" 
        for msg in plan 
        if msg.type == "plan" and msg.content
    ])
    
    # Prepare task parameters
    task_id = f"api_v1_execute_{session_id}"
    task_file_name = ""
    
    # Track current step for answer events
    current_step = 0
    in_step = False
    
    async def consume_stream():
        """Consume stream events and transform them to v1 API format"""
        nonlocal current_step, in_step
        
        try:
            while True:
                event = await stream_queue.get()
                if event is None:  # End of stream signal
                    # Close any open step
                    if in_step:
                        end_event = {"type": "end", "step": current_step, "delta": ""}
                        yield json.dumps(end_event, ensure_ascii=False) + "\n"
                    break
                
                event_type = event.get("event")
                data = event.get("data", {})
                
                # Transform events to v1 API format
                if event_type == "message":
                    # Messages are assistant responses (answer phase)
                    delta_content = data.get("delta", {}).get("content", "")
                    if delta_content:
                        # Start a new step if not in one
                        if not in_step:
                            current_step += 1
                            in_step = True
                            start_event = {"type": "start", "step": current_step, "delta": ""}
                            yield json.dumps(start_event, ensure_ascii=False) + "\n"
                        
                        # Send answer delta
                        answer_event = {"type": "answer", "step": current_step, "delta": delta_content}
                        yield json.dumps(answer_event, ensure_ascii=False) + "\n"
                
                elif event_type == "tool_call":
                    # Tool calls during execution might indicate new steps
                    tool_name = data.get("tool_name", "")
                    
                    # Close previous step if open
                    if in_step:
                        end_event = {"type": "end", "step": current_step, "delta": ""}
                        yield json.dumps(end_event, ensure_ascii=False) + "\n"
                        in_step = False
                
                elif event_type == "end_of_workflow":
                    # Workflow end
                    if in_step:
                        end_event = {"type": "end", "step": current_step, "delta": ""}
                        yield json.dumps(end_event, ensure_ascii=False) + "\n"
                    break
                
        except Exception as e:
            logger.error(f"Error in execute stream consumer: {e}", exc_info=True)
    
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


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}


@app.post("/v1/api/plan")
async def plan_endpoint(
    request: PlanRequest,
    x_session_id: str = Header(..., alias="X-Session-Id"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """
    Submit a question and generate a step-by-step plan.
    
    Args:
        request: Request body containing query and optional history
        x_session_id: Session ID from header
        authorization: Optional authorization token
    
    Returns:
        StreamingResponse with plan in streaming format:
        - {"type": "start", "step": 1, "delta": ""}
        - {"type": "plan", "step": 1, "delta": "xxx"}
        - {"type": "end", "step": 1, "delta": ""}
    """
    try:
        logger.info(f"Plan request for session {x_session_id}: {request.query}")
        
        return StreamingResponse(
            plan_stream_generator(
                query=request.query,
                history=request.history,
                session_id=x_session_id,
            ),
            media_type="application/x-ndjson",
        )
    
    except Exception as e:
        logger.error(f"Error processing plan request: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/api/execute")
async def execute_endpoint(
    request: ExecuteRequest,
    x_session_id: Optional[str] = Header(None, alias="X-Session-Id"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """
    Execute a plan and generate results.
    
    Args:
        request: Request body containing plan
        x_session_id: Optional session ID from header
        authorization: Optional authorization token
    
    Returns:
        StreamingResponse with execution results in streaming format:
        - {"type": "start", "step": 1, "delta": ""}
        - {"type": "answer", "step": 1, "delta": "xxx"}
        - {"type": "end", "step": 1, "delta": ""}
    """
    try:
        # Use provided session_id or generate a default one
        session_id = x_session_id or "default_execute_session"
        logger.info(f"Execute request for session {session_id}")
        
        return StreamingResponse(
            execute_stream_generator(
                plan=request.plan,
                session_id=session_id,
            ),
            media_type="application/x-ndjson",
        )
    
    except Exception as e:
        logger.error(f"Error processing execute request: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/api/upload")
async def upload_endpoint(
    file: UploadFile = File(...),
    x_session_id: str = Header(..., alias="X-Session-Id"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """
    Upload a file to the workspace.
    
    Args:
        file: The file to upload (from multipart/form-data)
        x_session_id: Session ID from header
        authorization: Optional authorization token
    
    Returns:
        JSON response indicating success
        Example: {"status": "success", "message": "File uploaded successfully"}
    
    Note:
        - Each X-Session-Id corresponds to a sandbox
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
        
        logger.info(f"Upload request for session {x_session_id}: {safe_filename}")
        
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
                "status": "success",
                "message": "File uploaded successfully"
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
        python api_server_v1.py llm=qwen-3 llm.api_key=xxxxx llm.base_url=http://localhost:8000/v1
    """
    global _default_cfg
    
    # Store the CLI-provided config as default
    _default_cfg = cfg
    
    logger.info("=" * 50)
    logger.info("MiroFlow Agent API Server v1 - Configuration")
    logger.info("=" * 50)
    logger.info(OmegaConf.to_yaml(cfg))
    logger.info("=" * 50)
    
    import uvicorn
    
    # Get host and port from environment or use defaults
    host = os.getenv("API_HOST", DEFAULT_HOST)
    port = int(os.getenv("API_PORT", str(DEFAULT_PORT)))
    
    logger.info(f"Starting server on {host}:{port}")
    
    # Run the FastAPI server
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
