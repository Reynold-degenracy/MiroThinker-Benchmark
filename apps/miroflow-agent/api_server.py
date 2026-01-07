# Copyright (c) 2025 MiroMind
# This source code is licensed under the MIT License.

import asyncio
import hashlib
import json
import logging
import sys
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

import hydra
from fastapi import FastAPI, Header, HTTPException
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
        main_agent_tool_manager, sub_agent_tool_managers, output_formatter = (
            create_pipeline_components(cfg)
        )
        _sessions[session_key] = {
            "main_agent_tool_manager": main_agent_tool_manager,
            "sub_agent_tool_managers": sub_agent_tool_managers,
            "output_formatter": output_formatter,
            "cfg": cfg,
        }
    
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
                    
                    # show_text tool is used to display answers
                    if tool_name == "show_text":
                        text = tool_input.get("text", "")
                        if text:
                            # Split by newlines to stream each part
                            for line in text.split('\n'):
                                if line:  # Skip empty lines
                                    output = {
                                        "status": "answer",
                                        "data": line + '\n'
                                    }
                                    yield json.dumps(output) + "\n"
                    
                    # show_error is for errors, also treat as answer
                    elif tool_name == "show_error":
                        error_text = tool_input.get("error", "")
                        if error_text:
                            output = {
                                "status": "answer",
                                "data": f"Error: {error_text}"
                            }
                            yield json.dumps(output) + "\n"
                    
                    # Other tool calls are planning steps
                    else:
                        if in_plan_phase:
                            current_step += 1
                            plan_text = f"Using tool: {tool_name}"
                            if tool_input:
                                # Truncate large inputs for display
                                input_str = json.dumps(tool_input)
                                if len(input_str) > 200:
                                    input_str = input_str[:200] + "..."
                                plan_text += f" with input: {input_str}"
                            
                            output = {
                                "status": "plan",
                                "step": current_step,
                                "data": plan_text
                            }
                            yield json.dumps(output) + "\n"
                
                elif event_type == "message":
                    # Messages are assistant responses (answer phase)
                    delta_content = data.get("delta", {}).get("content", "")
                    if delta_content:
                        output = {
                            "status": "answer",
                            "data": delta_content
                        }
                        yield json.dumps(output) + "\n"
                
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
                        yield json.dumps(output) + "\n"
                
                elif event_type == "start_of_workflow":
                    # Workflow start
                    if in_plan_phase:
                        current_step += 1
                        output = {
                            "status": "plan",
                            "step": current_step,
                            "data": "Workflow started"
                        }
                        yield json.dumps(output) + "\n"
                
                elif event_type == "end_of_workflow":
                    # Workflow end - signal completion
                    break
                
        except Exception as e:
            logger.error(f"Error in stream consumer: {e}", exc_info=True)
            error_output = {
                "status": "answer",
                "data": f"Error: {str(e)}"
            }
            yield json.dumps(error_output) + "\n"
    
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
