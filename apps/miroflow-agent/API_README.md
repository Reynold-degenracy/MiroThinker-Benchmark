# MiroFlow Agent API

This API provides a streaming endpoint for the MiroFlow Agent to process queries and return responses in real-time.

## Running the Server

### Basic Usage

```bash
# From the miroflow-agent directory
python3 api_server.py
```

The server will start on `http://localhost:8000` by default.

### With Hydra Configuration Overrides

You can override Hydra configuration settings when starting the server using command-line arguments:

```bash
# Override LLM provider and settings
python3 api_server.py llm=qwen-3 llm.base_url=http://localhost:61002/v1

# Override multiple settings
python3 api_server.py llm=qwen-3 llm.api_key=xxxxx llm.base_url=http://localhost:61002/v1 llm.temperature=0.7

# Use Claude with custom API key
python3 api_server.py llm=claude-3-7 llm.api_key=your-api-key-here

# Use GPT-5
python3 api_server.py llm=gpt-5 llm.api_key=your-openai-key
```

These CLI overrides set the **default configuration** for the server. All API requests will use these settings unless overridden per-request using the `config_overrides` parameter (see [Configuration Overrides](#configuration-overrides) section).

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
  "is_confirmed": false,
  "config_overrides": {
    "llm.provider": "qwen",
    "llm.base_url": "http://localhost:8000/v1"
  }
}
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| query | string | Yes | User's question |
| history | array | No | Conversation history (elements contain role and content). **Note: Currently not supported - each request starts a new conversation** |
| is_confirmed | bool | Yes | Whether the plan is confirmed. `false`: continue planner flow, `true`: execute actor flow |
| config_overrides | object | No | Hydra configuration overrides (e.g., LLM provider, model, base_url). See [Configuration Overrides](#configuration-overrides) section |

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

## POST /upload_file

Upload a file to the `/home/user/user_files/` directory.

### Headers

- `Content-Type: multipart/form-data`
- `X-Session-Id`: Session identifier (e.g., `sess_001`)

### Request Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| file | file | Yes | The file to upload from the client |

### Response Format

```json
{
  "data": {
    "path": "/home/user/user_files/example.txt"
  }
}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| data.path | string | The file path in the sandbox |

### Example Usage

#### Using curl

```bash
curl http://localhost:8000/upload_file \
  -H "X-Session-Id: sess_001" \
  -F "file=@/path/to/example.txt"
```

#### Using Python

```python
import requests

url = "http://localhost:8000/upload_file"
headers = {
    "X-Session-Id": "sess_001"
}

# Upload a file
with open("/path/to/example.txt", "rb") as f:
    files = {"file": f}
    response = requests.post(url, headers=headers, files=files)
    print(response.json())
    # Output: {"data": {"path": "/home/user/user_files/example.txt"}}
```

### Sandbox Notes

- Each `X-Session-Id` corresponds to a sandbox
- Sandbox lifecycle is 3600 seconds (default TTL), after which a new sandbox is automatically created
- The server is stateless and does not maintain conversation history
- Clients should maintain their own history and pass it in requests via the `history` parameter

## Health Check

```bash
curl http://localhost:8000/health
```

Returns: `{"status": "healthy"}`

## Configuration Overrides

You can override Hydra configuration settings on a per-request basis using the `config_overrides` parameter. This allows you to dynamically change LLM providers, models, API endpoints, and other settings without modifying configuration files.

### Common Override Examples

**Use a different LLM provider:**
```json
{
  "query": "What is 2+2?",
  "is_confirmed": false,
  "config_overrides": {
    "llm": "qwen-3",
    "llm.base_url": "http://localhost:61002/v1"
  }
}
```

**Override specific LLM parameters:**
```json
{
  "query": "What is 2+2?",
  "is_confirmed": false,
  "config_overrides": {
    "llm.temperature": "0.7",
    "llm.max_tokens": "8192",
    "llm.model_name": "custom-model"
  }
}
```

**Use Claude with custom API key:**
```json
{
  "query": "What is 2+2?",
  "is_confirmed": false,
  "config_overrides": {
    "llm": "claude-3-7",
    "llm.api_key": "your-api-key-here"
  }
}
```

**Complete example with curl:**
```bash
curl http://localhost:8000/get_response \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: sess_custom_llm" \
  -d '{
    "query": "What is the capital of France?",
    "is_confirmed": false,
    "config_overrides": {
      "llm": "qwen-3",
      "llm.base_url": "http://localhost:61002/v1",
      "llm.temperature": "0.5"
    }
  }'
```

### Available Configuration Keys

The most commonly used configuration keys for overrides:

**LLM Configuration:**
- `llm`: LLM config name (e.g., "qwen-3", "claude-3-7", "gpt-5")
- `llm.provider`: Provider name ("qwen", "anthropic", "openai")
- `llm.model_name`: Model name string
- `llm.base_url`: API base URL
- `llm.api_key`: API key
- `llm.temperature`: Temperature (0.0-1.0)
- `llm.max_tokens`: Maximum tokens to generate
- `llm.top_p`: Top-p sampling parameter
- `llm.top_k`: Top-k sampling parameter

**Agent Configuration:**
- `agent`: Agent config name (e.g., "single_agent_keep5", "mirothinker_v1.5_keep5_max200")
- `agent.main_agent.max_turns`: Maximum agent turns

See the `conf/` directory for all available configuration options.

## Session Management

Each session is identified by the `X-Session-Id` header. The server maintains separate pipeline components for each session, allowing multiple concurrent users.

**Note:** When using `config_overrides`, each unique combination of session ID and configuration creates a separate session instance. This ensures that different configurations don't interfere with each other.

## Configuration

The server uses the default Hydra configuration from the `conf/` directory. You can modify the configuration by editing the config files or passing environment variables.

## Notes

- The server requires all dependencies from `pyproject.toml` to be installed
- Environment variables should be configured in `.env` file
- The `is_confirmed` flag controls whether the response is in planning mode or execution mode
