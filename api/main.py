# Copyright (c) 2025 MiroMind
# This source code is licensed under the MIT License.

"""
FastAPI server for MiroFlow Agent with new API structure.

This module provides three main endpoints:
1. POST /v1/api/plan - Submit query and get planning steps (streaming)
2. POST /v1/api/execute - Execute plan and get results (streaming)  
3. POST /v1/api/upload - Upload files to sandbox

Startup:
    uv run uvicorn api.main:app --host 0.0.0.0 --port 8000
"""

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

# Add apps/miroflow-agent to path for imports
miroflow_agent_path = Path(__file__).parent.parent / "apps" / "miroflow-agent"
sys.path.insert(0, str(miroflow_agent_path))

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
        config_path = str(miroflow_agent_path / "conf")
        with hydra.initialize(config_path=config_path, version_base=None):
            _cfg = hydra.compose(config_name="config")
    
    # If overrides are provided, create a new config with those overrides
    if overrides:
        config_path = str(miroflow_agent_path / "conf")
        with hydra.initialize(config_path=config_path, version_base=None):
            return hydra.compose(config_name="config", overrides=overrides)
    
    return _cfg


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


# Request/Response Models
class Message(BaseModel):
    """Message format for requests and responses"""
    type: str  # query, plan, answer, action, start, end
    step: int = -1
    content: Optional[str] = None
    delta: Optional[str] = None


class PlanRequest(BaseModel):
    """Request model for /v1/api/plan endpoint"""
    query: str
    history: Optional[List[Message]] = None


class ExecuteRequest(BaseModel):
    """Request model for /v1/api/execute endpoint"""
    plan: List[Message]


async def stream_plan_generator(
    query: str,
    history: Optional[List[Message]],
    session_id: str,
):
    """
    Generate streaming plan responses in the required format.
    
    Transforms internal streaming events to:
    - {"type":"start", "step": N, "delta":""}
    - {"type":"plan", "step": N, "delta":"..."}
    - {"type":"end", "step": N, "delta":""}
    
    Args:
        query: User query
        history: Conversation history (currently not used)
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
    task_id = f"api_{session_id}"
    task_description = query
    task_file_name = ""
    
    # Track current step for plan events
    current_step = 0
    
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
                
                # Transform events to required format
                if event_type == "tool_call":
                    tool_name = data.get("tool_name", "")
                    tool_input = data.get("tool_input", data.get("delta_input", {}))
                    
                    # Skip show_text and show_error in plan phase
                    if tool_name in ["show_text", "show_error"]:
                        pass
                    else:
                        # Start of new step
                        current_step += 1
                        # Send start event
                        output = {"type": "start", "step": current_step, "delta": ""}
                        yield json.dumps(output, ensure_ascii=False) + "\n"
                        
                        # Send plan content
                        plan_text = f"Using tool: {tool_name}"
                        if tool_input:
                            # Truncate large inputs for display
                            input_str = json.dumps(tool_input, ensure_ascii=False)
                            if len(input_str) > 200:
                                input_str = input_str[:200] + "..."
                            plan_text += f" with input: {input_str}"
                        
                        output = {"type": "plan", "step": current_step, "delta": plan_text}
                        yield json.dumps(output, ensure_ascii=False) + "\n"
                        
                        # Send end event
                        output = {"type": "end", "step": current_step, "delta": ""}
                        yield json.dumps(output, ensure_ascii=False) + "\n"
                
                elif event_type == "start_of_agent":
                    # Starting an agent indicates planning
                    current_step += 1
                    agent_name = data.get("display_name", data.get("agent_name", "agent"))
                    
                    # Send start event
                    output = {"type": "start", "step": current_step, "delta": ""}
                    yield json.dumps(output, ensure_ascii=False) + "\n"
                    
                    # Send plan content
                    output = {"type": "plan", "step": current_step, "delta": f"Starting agent: {agent_name}"}
                    yield json.dumps(output, ensure_ascii=False) + "\n"
                    
                    # Send end event
                    output = {"type": "end", "step": current_step, "delta": ""}
                    yield json.dumps(output, ensure_ascii=False) + "\n"
                
                elif event_type == "start_of_workflow":
                    # Workflow start
                    current_step += 1
                    
                    # Send start event
                    output = {"type": "start", "step": current_step, "delta": ""}
                    yield json.dumps(output, ensure_ascii=False) + "\n"
                    
                    # Send plan content
                    output = {"type": "plan", "step": current_step, "delta": "Workflow started"}
                    yield json.dumps(output, ensure_ascii=False) + "\n"
                    
                    # Send end event
                    output = {"type": "end", "step": current_step, "delta": ""}
                    yield json.dumps(output, ensure_ascii=False) + "\n"
                
                elif event_type == "end_of_workflow":
                    # Workflow end - signal completion
                    break
        
        except Exception as e:
            logger.error(f"Error in stream consumer: {e}", exc_info=True)
            error_output = {"type": "plan", "step": current_step, "delta": f"Error: {str(e)}"}
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


async def stream_execute_generator(
    plan: List[Message],
    session_id: str,
):
    """
    Generate streaming execution responses in the required format.
    
    Transforms internal streaming events to:
    - {"type":"start", "step": N, "delta":""}
    - {"type":"answer", "step": N, "delta":"..."}
    - {"type":"end", "step": N, "delta":""}
    
    Args:
        plan: Plan steps to execute
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
    
    # Convert plan to task description
    task_description = "\n".join([f"Step {msg.step}: {msg.content}" for msg in plan if msg.content])
    
    # Prepare task parameters
    task_id = f"api_{session_id}_execute"
    task_file_name = ""
    
    # Track current step for answer events
    current_step = 0
    
    async def consume_stream():
        """Consume stream events and transform them"""
        nonlocal current_step
        
        try:
            step_started = False
            
            while True:
                event = await stream_queue.get()
                if event is None:  # End of stream signal
                    # If a step was started, send end event
                    if step_started:
                        output = {"type": "end", "step": current_step, "delta": ""}
                        yield json.dumps(output, ensure_ascii=False) + "\n"
                    break
                
                event_type = event.get("event")
                data = event.get("data", {})
                
                # Transform events to required format
                if event_type == "message":
                    # Messages are assistant responses (answer phase)
                    delta_content = data.get("delta", {}).get("content", "")
                    if delta_content:
                        # Start new step if not started
                        if not step_started:
                            current_step += 1
                            output = {"type": "start", "step": current_step, "delta": ""}
                            yield json.dumps(output, ensure_ascii=False) + "\n"
                            step_started = True
                        
                        # Send answer delta
                        output = {"type": "answer", "step": current_step, "delta": delta_content}
                        yield json.dumps(output, ensure_ascii=False) + "\n"
                
                elif event_type == "tool_call":
                    tool_name = data.get("tool_name", "")
                    tool_input = data.get("tool_input", data.get("delta_input", {}))
                    
                    # show_text tool is used to display answers
                    if tool_name == "show_text":
                        text = tool_input.get("text", "")
                        if text:
                            # End previous step if needed
                            if step_started:
                                output = {"type": "end", "step": current_step, "delta": ""}
                                yield json.dumps(output, ensure_ascii=False) + "\n"
                                step_started = False
                            
                            # Start new step
                            current_step += 1
                            output = {"type": "start", "step": current_step, "delta": ""}
                            yield json.dumps(output, ensure_ascii=False) + "\n"
                            step_started = True
                            
                            # Send answer
                            output = {"type": "answer", "step": current_step, "delta": text}
                            yield json.dumps(output, ensure_ascii=False) + "\n"
                            
                            # End step
                            output = {"type": "end", "step": current_step, "delta": ""}
                            yield json.dumps(output, ensure_ascii=False) + "\n"
                            step_started = False
                    
                    # show_error is for errors
                    elif tool_name == "show_error":
                        error_text = tool_input.get("error", "")
                        if error_text:
                            # End previous step if needed
                            if step_started:
                                output = {"type": "end", "step": current_step, "delta": ""}
                                yield json.dumps(output, ensure_ascii=False) + "\n"
                                step_started = False
                            
                            # Start new step
                            current_step += 1
                            output = {"type": "start", "step": current_step, "delta": ""}
                            yield json.dumps(output, ensure_ascii=False) + "\n"
                            
                            # Send error as answer
                            output = {"type": "answer", "step": current_step, "delta": f"Error: {error_text}"}
                            yield json.dumps(output, ensure_ascii=False) + "\n"
                            
                            # End step
                            output = {"type": "end", "step": current_step, "delta": ""}
                            yield json.dumps(output, ensure_ascii=False) + "\n"
                            step_started = False
                
                elif event_type == "end_of_workflow":
                    # Workflow end - signal completion
                    if step_started:
                        output = {"type": "end", "step": current_step, "delta": ""}
                        yield json.dumps(output, ensure_ascii=False) + "\n"
                    break
        
        except Exception as e:
            logger.error(f"Error in stream consumer: {e}", exc_info=True)
            if step_started:
                output = {"type": "end", "step": current_step, "delta": ""}
                yield json.dumps(output, ensure_ascii=False) + "\n"
            error_output = {"type": "answer", "step": current_step + 1, "delta": f"Error: {str(e)}"}
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


@app.post("/v1/api/plan")
async def plan_endpoint(
    request: PlanRequest,
    x_session_id: str = Header(..., alias="X-Session-Id"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """
    Submit a question and stream planning steps.
    
    Args:
        request: Request body containing query and optional history
        x_session_id: Session ID from header
        authorization: Authorization token from header (optional)
    
    Returns:
        StreamingResponse with NDJSON format:
        - {"type":"start", "step": N, "delta":""}
        - {"type":"plan", "step": N, "delta":"..."}
        - {"type":"end", "step": N, "delta":""}
    """
    try:
        logger.info(f"Received plan request for session {x_session_id}: {request.query}")
        
        # Note: history parameter is accepted but not currently used
        if request.history:
            logger.warning(
                f"History parameter provided but not currently supported. "
                f"Starting new conversation for session {x_session_id}"
            )
        
        return StreamingResponse(
            stream_plan_generator(
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
    Execute a plan and stream results.
    
    Args:
        request: Request body containing plan
        x_session_id: Session ID from header (optional)
        authorization: Authorization token from header (optional)
    
    Returns:
        StreamingResponse with NDJSON format:
        - {"type":"start", "step": N, "delta":""}
        - {"type":"answer", "step": N, "delta":"..."}
        - {"type":"end", "step": N, "delta":""}
    """
    try:
        # Use session ID or generate a default one
        session_id = x_session_id or "default_execute_session"
        logger.info(f"Received execute request for session {session_id}")
        
        return StreamingResponse(
            stream_execute_generator(
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
    Upload a file directly to the E2B sandbox for the given session.
    
    Args:
        file: The file to upload (from multipart/form-data)
        x_session_id: Session ID from header
        authorization: Authorization token from header (optional)
    
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


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    
    logger.info("=" * 50)
    logger.info("MiroFlow Agent API Server - Starting")
    logger.info("=" * 50)
    
    # Run the FastAPI server
    uvicorn.run(app, host="0.0.0.0", port=8000)
