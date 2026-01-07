# MiroFlow Agent API

This API provides a streaming endpoint for the MiroFlow Agent to process queries and return responses in real-time.

## Running the Server

```bash
# From the miroflow-agent directory
python3 api_server.py
```

The server will start on `http://localhost:8000` by default.

## API Endpoint

### POST /get_response

Submit a question and receive streaming responses.

#### Headers

- `Content-Type: application/json`
- `X-Session-Id`: Session identifier (e.g., `sess_001`)

#### Request Body

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

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| query | string | Yes | User's question |
| history | array | No | Conversation history (elements contain role and content). **Note: Currently not supported - each request starts a new conversation** |
| is_confirmed | bool | Yes | Whether the plan is confirmed. `false`: continue planner flow, `true`: execute actor flow |

#### Response Format

The response is streamed in NDJSON (Newline Delimited JSON) format:

```json
{"status":"plan", "step": 1, "data":"Workflow started"}
{"status":"plan", "step": 2, "data":"Using tool: search with input: {...}"}
{"status":"answer", "data":"I am"}
{"status":"answer", "data":" Manus"}
{"status":"answer", "data":"\n"}
{"status":"answer", "data":"The answer is..."}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| status | "plan" or "answer" | Event type - "plan" for planning steps, "answer" for response text |
| step | int (optional) | Current step number (only present when status="plan") |
| data | string | Text fragment |

## Example Usage

### Using curl

```bash
curl http://localhost:8000/get_response \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: sess_001" \
  -d '{
    "query": "user_files 文件夹里的文件主题是什么",
    "history": [
      {"role": "user", "content": "你好"},
      {"role": "assistant", "content": "你好，有什么可以帮你？"}
    ],
    "is_confirmed": false
  }'
```

### Using Python

```python
import requests
import json

url = "http://localhost:8000/get_response"
headers = {
    "Content-Type": "application/json",
    "X-Session-Id": "sess_001"
}
data = {
    "query": "user_files 文件夹里的文件主题是什么",
    "history": [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好，有什么可以帮你？"}
    ],
    "is_confirmed": False
}

response = requests.post(url, headers=headers, json=data, stream=True)

for line in response.iter_lines():
    if line:
        event = json.loads(line)
        print(f"[{event['status']}] {event.get('step', '')}: {event['data']}")
```

## Health Check

```bash
curl http://localhost:8000/health
```

Returns: `{"status": "healthy"}`

## Session Management

Each session is identified by the `X-Session-Id` header. The server maintains separate pipeline components for each session, allowing multiple concurrent users.

## Configuration

The server uses the default Hydra configuration from the `conf/` directory. You can modify the configuration by editing the config files or passing environment variables.

## Notes

- The server requires all dependencies from `pyproject.toml` to be installed
- Environment variables should be configured in `.env` file
- The `is_confirmed` flag controls whether the response is in planning mode or execution mode
