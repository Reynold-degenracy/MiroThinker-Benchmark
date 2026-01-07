# Copyright (c) 2025 MiroMind
# This source code is licensed under the MIT License.

import asyncio
import json
import logging
from typing import Dict, List, Optional

import hydra
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from omegaconf import DictConfig, OmegaConf
from pydantic import BaseModel

from src.core.pipeline import create_pipeline_components, execute_task_pipeline
from src.logging.task_logger import bootstrap_logger

# Configure logger
logger = bootstrap_logger()
app = FastAPI(title="MiroFlow Agent API")

# Global configuration storage
_cfg: Optional[DictConfig] = None
_sessions: Dict[str, Dict] = {}


class QueryRequest(BaseModel):
    query: str
    history: Optional[List[Dict[str, str]]] = None
    is_confirmed: bool


def initialize_config():
    """Initialize Hydra configuration"""
    global _cfg
    if _cfg is None:
        # Initialize Hydra with default config
        with hydra.initialize(config_path="conf", version_base=None):
            _cfg = hydra.compose(config_name="config")
    return _cfg


async def stream_generator(
    query: str,
    history: Optional[List[Dict[str, str]]],
    is_confirmed: bool,
    session_id: str,
):
    """
    Generate streaming responses in NDJSON format.
    
    Transforms internal streaming events to the required format:
    - {"status":"plan", "step": 1, "data":"..."}
    - {"status":"answer", "data":"..."}
    """
    # Create async queue for receiving streaming updates
    stream_queue = asyncio.Queue()
    
    # Get or create pipeline components for this session
    if session_id not in _sessions:
        cfg = initialize_config()
        main_agent_tool_manager, sub_agent_tool_managers, output_formatter = (
            create_pipeline_components(cfg)
        )
        _sessions[session_id] = {
            "main_agent_tool_manager": main_agent_tool_manager,
            "sub_agent_tool_managers": sub_agent_tool_managers,
            "output_formatter": output_formatter,
            "cfg": cfg,
        }
    
    session = _sessions[session_id]
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
        request: Request body containing query, history, and is_confirmed
        x_session_id: Session ID from header
    
    Returns:
        StreamingResponse with NDJSON format:
        - {"status":"plan", "step": 1, "data":"..."}
        - {"status":"answer", "data":"..."}
    """
    try:
        logger.info(f"Received request for session {x_session_id}: {request.query}")
        
        return StreamingResponse(
            stream_generator(
                query=request.query,
                history=request.history,
                is_confirmed=request.is_confirmed,
                session_id=x_session_id,
            ),
            media_type="application/x-ndjson",
        )
    
    except Exception as e:
        logger.error(f"Error processing request: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.on_event("startup")
async def startup_event():
    """Initialize configuration on startup"""
    logger.info("Starting MiroFlow Agent API Server")
    initialize_config()


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("Shutting down MiroFlow Agent API Server")
    # Clean up all sessions
    for session_id, session in _sessions.items():
        # Close tool managers if needed
        pass


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(app, host="0.0.0.0", port=8000)
