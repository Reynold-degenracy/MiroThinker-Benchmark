# Copyright (c) 2025 MiroMind
# This source code is licensed under the MIT License.

import asyncio
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
_sessions_lock = asyncio.Lock()


def _parse_command_result_stdout(result_str: str) -> Optional[str]:
    """
    Parse stdout from CommandResult string format.
    
    Example input: "CommandResult(stderr=, stdout='filename.png\n', exit_code=0, error=)"
    Returns: "filename.png"
    
    Args:
        result_str: The CommandResult string
        
    Returns:
        The stdout content, or None if parsing fails
    """
    import re
    
    # Match stdout=... pattern
    match = re.search(r'stdout=([^,\)]*)', result_str)
    if match:
        stdout_value = match.group(1).strip()
        # Remove escaped newline characters (\n, \r, etc.) that appear as literal strings
        # This handles cases where shell command output includes newlines
        stdout_value = stdout_value.replace('\\n', '').replace('\\r', '').strip()
        
        # Remove surrounding quotes (single or double) if present
        # CommandResult may wrap stdout value in quotes like: stdout='value' or stdout="value"
        if stdout_value and len(stdout_value) >= 2:
            if (stdout_value[0] == "'" and stdout_value[-1] == "'") or \
               (stdout_value[0] == '"' and stdout_value[-1] == '"'):
                stdout_value = stdout_value[1:-1].strip()
        
        return stdout_value if stdout_value else None
    
    return None


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
        shared_lock = self.session_dict.get("sandbox_creation_lock")
        if shared_lock is None:
            shared_lock = asyncio.Lock()
            self.session_dict["sandbox_creation_lock"] = shared_lock
        self._sandbox_creation_lock = shared_lock
        self._sandbox_tools = {
            "run_command",
            "run_python_code",
            "upload_file_from_local_to_sandbox",
        }
    
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
        if tool_name not in self._sandbox_tools:
            return False
        
        # Check if sandbox_id is missing or invalid
        sandbox_id = arguments.get("sandbox_id")
        if not sandbox_id:
            return True
        sandbox_id_normalized = str(sandbox_id).strip().lower()
        if not sandbox_id_normalized:
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
        if sandbox_id_normalized in INVALID_SANDBOX_IDS:
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
        # Restrict tool-python capabilities exposed to agents.
        if server_name == "tool-python" and tool_name not in self._sandbox_tools:
            allowed_tools = ", ".join(sorted(self._sandbox_tools))
            logger.warning(
                f"Blocked disallowed tool-python tool '{tool_name}'. Allowed tools: {allowed_tools}"
            )
            return {
                "server_name": server_name,
                "tool_name": tool_name,
                "error": (
                    f"Tool '{tool_name}' is disabled. "
                    f"Allowed tool-python tools: {allowed_tools}"
                ),
            }

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
            and tool_name in self._sandbox_tools
            and self._is_sandbox_error(result)
        ):
            # Capture the sandbox_id before acquiring the lock to detect concurrent updates
            sandbox_id = self.session_dict.get("sandbox_id")
            should_recreate = False
            
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
                should_recreate = True
                
            # Retry with new sandbox outside the lock to avoid nested lock deadlocks.
            if should_recreate:
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
    
    # Expose only a minimal Python tool subset to agents.
    blocked_python_tools = {
        "create_sandbox",
        "run_python_code_stream",
        "download_file_from_sandbox_to_local",
        "download_file_from_internet_to_sandbox",
    }
    for tool_name in blocked_python_tools:
        main_agent_tool_manager.tool_blacklist.add(("tool-python", tool_name))
    logger.info(
        "Applied tool-python blacklist to keep only run_command, run_python_code, "
        "upload_file_from_local_to_sandbox available to agents"
    )
    
    for sub_agent_tool_manager in sub_agent_tool_managers.values():
        for tool_name in blocked_python_tools:
            sub_agent_tool_manager.tool_blacklist.add(("tool-python", tool_name))
    
    # Create session dict
    session_dict = {
        "cfg": cfg,
        "sandbox_id": None,  # Will be created on first use
        "sandbox_creation_lock": asyncio.Lock(),
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


async def _get_or_create_session(
    session_id: str,
    config_overrides: Optional[Dict[str, str]] = None,
) -> Dict:
    """
    Get an existing session or create one atomically.

    Notes:
    - A single session_id always maps to exactly one session/sandbox.
    - config_overrides only apply when creating a new session.
    """
    override_list = None
    if config_overrides:
        override_list = [f"{key}={value}" for key, value in config_overrides.items()]

    async with _sessions_lock:
        session = _sessions.get(session_id)
        if session is not None:
            if config_overrides:
                logger.warning(
                    f"Ignoring config_overrides for existing session {session_id} to keep a single sandbox per session."
                )
            return session

        cfg = initialize_config(overrides=override_list)
        session = _create_session_with_wrapped_managers(cfg)
        _sessions[session_id] = session

    if override_list:
        logger.info(f"Created session {session_id} with config overrides: {override_list}")
    else:
        logger.info(f"Created session {session_id} with default config")

    return session


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


class ExecuteMessage(BaseModel):
    type: str
    step: Optional[int] = 1
    content: str


class ExecuteRequest(BaseModel):
    message: List[ExecuteMessage]
    # Optional Hydra configuration overrides
    config_overrides: Optional[Dict[str, str]] = None


def _validate_bearer_auth(authorization: Optional[str] = None) -> Optional[str]:
    """
    Validate Authorization header if provided. Authorization is optional.
    
    Args:
        authorization: Optional Authorization header value
        
    Returns:
        The token if authorization was provided and valid, None otherwise
    """
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization header; expected 'Bearer <token>'")
    token = parts[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    return token


def _validate_json_content_type(content_type: str) -> None:
    """Ensure Content-Type is application/json (charset allowed)."""
    if not content_type or "application/json" not in content_type.lower():
        raise HTTPException(status_code=415, detail="Content-Type must be application/json")


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


async def _download_sandbox_file_to_local(session: Dict) -> Optional[str]:
    """
    Download the first file from sandbox's /home/user/uploaded folder to a local temp path.
    
    Uses the download_file_from_sandbox_to_local tool to transfer files reliably.
    
    Args:
        session: The session dictionary containing tool_manager and sandbox_id
        
    Returns:
        Local file path if a file was downloaded, None otherwise.
        The caller is responsible for cleaning up the temp file after use.
    """
    tool_manager = session["main_agent_tool_manager"]
    # Internal post-processing path can use the underlying manager directly.
    # This avoids agent-facing tool restrictions while keeping output file download working.
    if isinstance(tool_manager, SessionAwareSandboxManager):
        tool_manager = tool_manager.tool_manager
    sandbox_id = session.get("sandbox_id")
    
    # If no sandbox exists yet, no files to download
    if not sandbox_id:
        logger.info("No sandbox exists for this session, skipping file download")
        return None
    
    sandbox_uploaded_dir = "/home/user/uploaded"
    
    try:
        # List files in the sandbox's uploaded directory (get only the first one)
        list_result = await tool_manager.execute_tool_call(
            server_name="tool-python",
            tool_name="run_command",
            arguments={
                "sandbox_id": sandbox_id,
                "command": f"ls -1 {sandbox_uploaded_dir} 2>/dev/null | head -1"
            }
        )
        
        if "result" not in list_result:
            logger.warning(f"Unexpected list result: {list_result}")
            return None
        
        result_str = list_result["result"]
        if "[ERROR]" in result_str:
            logger.warning(f"Failed to list sandbox files: {result_str}")
            return None
        
        # Parse stdout from CommandResult string
        file_name = _parse_command_result_stdout(result_str)
        if not file_name:
            logger.info("No files found in sandbox uploaded directory")
            return None
        
        logger.info(f"Found file in sandbox: {file_name}")
        
        sandbox_file_path = f"{sandbox_uploaded_dir}/{file_name}"
        
        # Use download_file_from_sandbox_to_local tool to download the file
        download_result = await tool_manager.execute_tool_call(
            server_name="tool-python",
            tool_name="download_file_from_sandbox_to_local",
            arguments={
                "sandbox_id": sandbox_id,
                "sandbox_file_path": sandbox_file_path,
                "local_filename": file_name
            }
        )
        
        if "result" not in download_result:
            logger.warning(f"Unexpected download result: {download_result}")
            return None
        
        result_str = download_result["result"]
        if "[ERROR]" in result_str:
            logger.warning(f"Failed to download file: {result_str}")
            return None
        
        # Parse the local file path from result
        # Expected format: "File downloaded successfully to: /path/to/file"
        if "File downloaded successfully to:" in result_str:
            local_file_path = result_str.split("File downloaded successfully to:")[-1].strip()
            logger.info(f"Successfully downloaded {file_name} to {local_file_path}")
            return local_file_path
        else:
            logger.warning(f"Unexpected download result format: {result_str}")
            return None
            
    except Exception as e:
        logger.error(f"Error downloading sandbox file: {e}", exc_info=True)
        return None


def _cleanup_temp_file(file_path: Optional[str]) -> None:
    """
    Clean up a temporary file and its parent directory.
    
    Args:
        file_path: Path to the temp file to clean up
    """
    if not file_path:
        return
    
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up temp file: {file_path}")
        
        # Also remove the temp directory if it's empty
        temp_dir = os.path.dirname(file_path)
        if temp_dir and os.path.exists(temp_dir) and not os.listdir(temp_dir):
            os.rmdir(temp_dir)
            logger.info(f"Cleaned up temp directory: {temp_dir}")
    except Exception as e:
        logger.warning(f"Failed to clean up temp file {file_path}: {e}")


async def stream_generator(
    messages: List[ExecuteMessage],
    session_id: str,
    config_overrides: Optional[Dict[str, str]] = None,
):
    """
    Generate streaming responses in NDJSON format.
    
    Output format:
    - {"type": "start", "step": 1, "delta": ""}
    - {"type": "answer", "step": 1, "delta": "..."}
    - {"type": "end", "step": 1, "delta": ""}
    """
    if not messages:
        raise HTTPException(status_code=400, detail="message is required")
    primary_message = next((msg for msg in messages if msg.type == "query"), messages[0])
    # Client expects step to stay at 1 in the stream
    step_value = 1
    query = primary_message.content
    # Create async queue for receiving streaming updates
    stream_queue = asyncio.Queue()

    # Get or create pipeline components for this session atomically.
    session = await _get_or_create_session(
        session_id=session_id,
        config_overrides=config_overrides,
    )
    cfg = session["cfg"]
    
    # Prepare task parameters
    task_id = f"api_{session_id}"
    task_description = query
    
    # Download file from sandbox /home/user/uploaded folder to local temp path
    task_file_name = await _download_sandbox_file_to_local(session)
    if task_file_name:
        logger.info(f"Downloaded file from sandbox for session {session_id}: {task_file_name}")
    
    async def consume_stream():
        """Consume stream events and transform them"""

        def _make_answer(delta_text: str) -> bytes:
            """Helper to build an NDJSON answer line."""
            return (json.dumps({"type": "answer", "step": step_value, "delta": delta_text}, ensure_ascii=False) + "\n").encode('utf-8')

        try:
            # Emit start event immediately
            start_event = json.dumps({"type": "start", "step": step_value, "delta": ""}, ensure_ascii=False) + "\n"
            logger.info(f"[Stream Output] Sending START event immediately")
            yield start_event.encode('utf-8')

            current_agent_name = None
            should_process_messages = True

            # --- MCP tool-call tag filtering state ---
            # inside_mcp_tool: True while we are between <use_mcp_tool> and </use_mcp_tool>
            inside_mcp_tool = False
            OPEN_TAG = "<use_mcp_tool>"
            CLOSE_TAG = "</use_mcp_tool>"
            # Sliding window of up to WINDOW_SIZE deltas for detecting tags
            # that may be split across many deltas by different tokenizers.
            # When the buffer exceeds WINDOW_SIZE, the oldest delta is flushed
            # to maintain true streaming output with bounded latency.
            WINDOW_SIZE = 10
            delta_buffer = []
            skip_prefix = 0

            def _flush_oldest_delta():
                """Flush the oldest delta from the buffer with tag filtering.

                Removes the oldest delta from delta_buffer, updates
                inside_mcp_tool, and returns the visible (non-muted) text."""
                nonlocal inside_mcp_tool, skip_prefix
                window = "".join(delta_buffer)
                first_len = len(delta_buffer[0])
                local_skip = skip_prefix
                new_skip_prefix = skip_prefix

                # Scan entire window for open/close tags, collecting visible
                # chars that fall within [0, first_len) – the oldest delta.
                visible_parts = []
                inside_scan = inside_mcp_tool
                scan_pos = 0

                while scan_pos < len(window):
                    if not inside_scan:
                        idx = window.find(OPEN_TAG, scan_pos)
                        if idx == -1:
                            # No open tag in rest of window
                            if scan_pos < first_len:
                                span_start = max(scan_pos, local_skip)
                                if span_start < first_len:
                                    visible_parts.append(
                                        window[span_start:first_len]
                                    )
                            break
                        else:
                            if scan_pos < first_len and idx > scan_pos:
                                span_start = max(scan_pos, local_skip)
                                span_end = min(idx, first_len)
                                if span_end > span_start:
                                    visible_parts.append(
                                        window[span_start:span_end]
                                    )
                            if idx < first_len:
                                if idx >= local_skip:
                                    visible_parts.append(OPEN_TAG)
                                new_skip_prefix = max(
                                    new_skip_prefix, idx + len(OPEN_TAG)
                                )
                            inside_scan = True
                            scan_pos = idx + len(OPEN_TAG)
                    else:
                        idx = window.find(CLOSE_TAG, scan_pos)
                        if idx == -1:
                            # Still inside mcp tool – skip rest
                            break
                        else:
                            if idx < first_len:
                                if idx >= local_skip:
                                    visible_parts.append(CLOSE_TAG)
                                new_skip_prefix = max(
                                    new_skip_prefix, idx + len(CLOSE_TAG)
                                )
                            inside_scan = False
                            scan_pos = idx + len(CLOSE_TAG)

                # Compute inside_mcp_tool state at the first_len boundary
                # so subsequent flushes start with the correct state.
                state = inside_mcp_tool
                s = 0
                while s < first_len:
                    if not state:
                        idx = window.find(OPEN_TAG, s)
                        if idx == -1 or idx >= first_len:
                            break
                        state = True
                        s = idx + len(OPEN_TAG)
                    else:
                        idx = window.find(CLOSE_TAG, s)
                        if idx == -1 or idx >= first_len:
                            break
                        state = False
                        s = idx + len(CLOSE_TAG)
                inside_mcp_tool = state

                delta_buffer.pop(0)
                skip_prefix = max(new_skip_prefix - first_len, 0)
                return "".join(visible_parts)

            while True:
                event = await stream_queue.get()
                if event is None:  # End of stream signal
                    break

                event_type = event.get("event")
                data = event.get("data", {})

                # Track current agent to skip Final Summary messages
                if event_type == "start_of_agent":
                    current_agent_name = data.get("agent_name", "")
                    should_process_messages = (current_agent_name != "Final Summary")
                    logger.debug(f"[Agent] Started: {current_agent_name}, process_messages={should_process_messages}")

                elif event_type == "end_of_agent":
                    agent_name = data.get("agent_name", "")
                    logger.debug(f"[Agent] Ended: {agent_name}")

                if event_type == "start_of_llm":
                    # New LLM turn – flush remaining buffered deltas and reset
                    while delta_buffer:
                        visible = _flush_oldest_delta()
                        if visible:
                            yield _make_answer(visible)
                    inside_mcp_tool = False
                    skip_prefix = 0

                if event_type == "tool_call":
                    tool_name = data.get("tool_name", "")
                    tool_input = data.get("tool_input", data.get("delta_input", {}))
                    if tool_name == "show_error":
                        error_text = tool_input.get("error", "")
                        if error_text:
                            logger.info("[Stream Output] Sending ERROR event")
                            yield _make_answer(f"Error: {error_text}")

                elif event_type == "message":
                    if not should_process_messages:
                        continue

                    delta_content = data.get("delta", {}).get("content", "")
                    if not delta_content:
                        continue

                    # ---- 10-delta sliding window tag detection ----
                    # Buffer the incoming delta.  When the buffer exceeds
                    # WINDOW_SIZE, flush the oldest delta so we maintain
                    # true streaming output with bounded latency.
                    delta_buffer.append(delta_content)

                    while len(delta_buffer) > WINDOW_SIZE:
                        visible = _flush_oldest_delta()
                        if visible:
                            yield _make_answer(visible)

                elif event_type == "end_of_workflow":
                    break

        except Exception as e:
            logger.error(f"Error in stream consumer: {e}", exc_info=True)
            logger.info("[Stream Output] Sending ERROR event due to exception")
            yield _make_answer(f"Error: {str(e)}")
        finally:
            # Flush all remaining buffered deltas
            while delta_buffer:
                visible = _flush_oldest_delta()
                if visible:
                    yield _make_answer(visible)
            end_event = json.dumps({"type": "end", "step": step_value, "delta": ""}, ensure_ascii=False) + "\n"
            logger.info(f"[Stream Output] Sending END event")
            yield end_event.encode('utf-8')
    
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
            # Clean up temp file after pipeline execution
            _cleanup_temp_file(task_file_name)
            # Signal end of stream
            await stream_queue.put(None)
    
    # Start pipeline in background
    asyncio.create_task(run_pipeline())
    
    # Yield transformed stream events as bytes
    async for output in consume_stream():
        yield output
        # Note: Yielding bytes directly helps with immediate flushing

# @app.post("/v1/api/plan")
# async def plan(
#     request: ExecuteRequest,
#     x_session_id: str = Header(..., alias="X-Session-Id"),
#     authorization: Optional[str] = Header(None, alias="Authorization"),
#     content_type: str = Header(..., alias="Content-Type"),
# ):
#     """Return the question from the request."""
#     _validate_json_content_type(content_type)
#     _validate_bearer_auth(authorization)
    
#     if not request.message:
#         raise HTTPException(status_code=400, detail="message is required")
    
#     primary_message = next((msg for msg in request.message if msg.type == "query"), request.message[0])
#     question = primary_message.content
    
#     logger.info(f"Plan endpoint hit for session {x_session_id}: {question}")
#     return {"question": question}


@app.post("/v1/api/execute")
async def execute(
    request: ExecuteRequest,
    x_session_id: str = Header(..., alias="X-Session-Id"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    content_type: str = Header(..., alias="Content-Type"),
):
    """
    Submit a question and stream Scheduler's full output.
    
    Expected headers:
    - Content-Type: application/json
    - X-Session-Id: <session>
    - Authorization: Bearer <token>
    
    Body example:
    {
      "message": [
        {"type": "query", "step": 1, "content": "xxx"}
      ]
    }
    
    Streamed response (NDJSON):
    {"type": "start", "step": 1, "delta": ""}
    {"type": "answer", "step": 1, "delta": "..."}
    {"type": "end", "step": 1, "delta": ""}
    """
    try:
        _validate_json_content_type(content_type)
        _validate_bearer_auth(authorization)
        if not request.message:
            raise HTTPException(status_code=400, detail="message is required")
        primary_message = next((msg for msg in request.message if msg.type == "query"), request.message[0])
        logger.info(f"Received execute request for session {x_session_id}: {primary_message.content}")
        
        if request.config_overrides:
            logger.info(f"Config overrides provided: {request.config_overrides}")
        
        return StreamingResponse(
            stream_generator(
                messages=request.message,
                session_id=x_session_id,
                config_overrides=request.config_overrides,
            ),
            media_type="application/x-ndjson",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing request: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}


@app.post("/v1/api/upload")
async def upload(
    file: UploadFile = File(...),
    x_session_id: str = Header(..., alias="X-Session-Id"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """
    Upload a file directly to the E2B sandbox for the given session.
    
    Args:
        file: The file to upload (from multipart/form-data)
        x_session_id: Session ID from header
        authorization: Bearer token header
    
    Returns:
        JSON response with the file path in the sandbox
        Example: {"data": {"path": "/home/user/uploaded/example.txt", "sandbox_id": "abc123"}}
    
    Note:
        - Each X-Session-Id corresponds to a session with a sandbox
        - Files are uploaded directly to the E2B sandbox, not the API server
        - Sandbox lifecycle is 3600 seconds (default TTL)
        - The server is stateless and does not maintain conversation history
        - Endpoint available at /v1/api/upload
    """
    try:
        _validate_bearer_auth(authorization)
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

        # Get or create session atomically.
        session = await _get_or_create_session(session_id=x_session_id)
        tool_manager = session["main_agent_tool_manager"]
        
        # The SessionAwareSandboxManager will automatically create and inject sandbox_id
        # So we can directly call the upload tool without managing sandbox_id manually
        
        async def upload_to_sandbox(local_path: str):
            """Upload file to the session's sandbox (sandbox_id auto-injected)."""
            sandbox_file_path = f"/home/user/uploaded/{safe_filename}"
            logger.info(f"Uploading {local_path} to session sandbox at {sandbox_file_path}")
            
            # Note: sandbox_id will be automatically injected by SessionAwareSandboxManager
            upload_result = await tool_manager.execute_tool_call(
                server_name="tool-python",
                tool_name="upload_file_from_local_to_sandbox",
                arguments={
                    # sandbox_id is auto-injected, no need to pass it explicitly
                    "local_file_path": local_path,
                    "sandbox_file_path": "/home/user/uploaded"
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
                            "command": f"mv /home/user/uploaded/{uploaded_temp_name} /home/user/uploaded/{safe_filename}"
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
    uvicorn.run(app, host="0.0.0.0", port=7210)


if __name__ == "__main__":
    main()
