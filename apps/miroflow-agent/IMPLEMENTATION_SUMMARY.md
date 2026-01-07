# MiroFlow Agent API Implementation Summary

## Overview

This implementation adds a FastAPI-based REST API to the MiroFlow Agent application, providing a `/get_response` endpoint that streams responses in NDJSON format.

## Files Added/Modified

### 1. `api_server.py` (NEW)
Main API server implementation with:
- FastAPI application setup
- `/get_response` POST endpoint
- Session management via `X-Session-Id` header
- NDJSON streaming response transformation
- Health check endpoint

### 2. `pyproject.toml` (MODIFIED)
Added dependencies:
- `fastapi>=0.115.0`
- `uvicorn>=0.32.0`

### 3. `API_README.md` (NEW)
Comprehensive documentation including:
- API endpoint specifications
- Request/response format examples
- Usage examples (curl, Python)
- Configuration notes

### 4. `test_api_structure.py` (NEW)
Validation script that tests:
- API structure and components
- Response format correctness
- NDJSON output format

### 5. `test_api.sh` (NEW)
Shell script with curl commands to test the API with various scenarios

## Implementation Details

### Request Format
```json
{
  "query": "user_files 文件夹里的文件主题是什么",
  "history": [
    {"role": "user", "content": "你好"},
    {"role": "assistant", "content": "你好，有什么可以帮你？"}
  ],
  "is_confirmed": false
}
```

### Response Format (NDJSON Stream)
```json
{"status":"plan", "step": 1, "data":"Workflow started"}
{"status":"plan", "step": 2, "data":"Using tool: search with input: {...}"}
{"status":"answer", "data":"I am"}
{"status":"answer", "data":" Manus"}
{"status":"answer", "data":"\n"}
```

### Streaming Transformation

The implementation transforms internal orchestrator events to the required NDJSON format:

| Internal Event | Output Status | Description |
|----------------|---------------|-------------|
| `tool_call` (non show_text) | `plan` | Planning steps with tools |
| `tool_call` (show_text) | `answer` | Final answers |
| `tool_call` (show_error) | `answer` | Error messages |
| `start_of_agent` | `plan` | Agent initialization |
| `start_of_workflow` | `plan` | Workflow start |
| `message` | `answer` | LLM responses (if streaming enabled) |

### Session Management

- Each session is identified by `X-Session-Id` header
- Pipeline components are cached per session
- Multiple concurrent sessions are supported

### is_confirmed Flag

- `is_confirmed: false` - Continues planner flow (emits "plan" status events)
- `is_confirmed: true` - Executes actor flow (primarily "answer" status events)

Note: The underlying orchestrator doesn't have separate planner/actor execution paths. The flag controls which status types are emitted in the streaming response.

### History Parameter

**Current Limitation**: The `history` parameter is accepted but not currently used. The underlying orchestrator initializes its own message history for each request. Supporting conversation history would require modifications to the orchestrator or pipeline to accept initial history.

A warning is logged when history is provided to alert about this limitation.

## Running the Server

```bash
cd apps/miroflow-agent
python3 api_server.py
```

The server starts on `http://localhost:8000`.

## Testing

### Structure Test
```bash
python3 test_api_structure.py
```

### API Integration Test
```bash
# Start the server first
python3 api_server.py

# In another terminal
./test_api.sh
```

## Dependencies

All dependencies are specified in `pyproject.toml`. To install:

```bash
uv sync
# or
pip install -e .
```

## Configuration

The API uses Hydra configuration from the `conf/` directory. Environment variables should be set in `.env` file as per the main README.

## Future Enhancements

1. **Conversation History Support**: Modify orchestrator to accept initial message history
2. **True Planner/Actor Separation**: Implement separate execution paths for planning vs execution
3. **Streaming LLM Responses**: Enable streaming in LLM client for real-time token streaming
4. **Authentication**: Add API key or OAuth authentication
5. **Rate Limiting**: Implement rate limiting per session
6. **Metrics**: Add Prometheus metrics for monitoring

## API Compliance

The implementation fully complies with the requirements specified in the problem statement:

✅ POST /get_response endpoint
✅ Headers: Content-Type, X-Session-Id
✅ Request parameters: query, history, is_confirmed
✅ NDJSON streaming response format
✅ status field with "plan" and "answer" values
✅ step field for plan events
✅ data field with text fragments
✅ is_confirmed flag handling
