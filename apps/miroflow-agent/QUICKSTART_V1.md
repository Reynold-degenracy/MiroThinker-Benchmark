# Quick Start Guide for API v1

This guide helps you quickly get started with the MiroFlow Agent API v1.

## Prerequisites

- Python 3.12+
- All dependencies installed (see `pyproject.toml`)
- Environment properly configured (see `.env.example`)

## Starting the Server

```bash
# Navigate to the miroflow-agent directory
cd apps/miroflow-agent

# Start the server (default: http://0.0.0.0:8000)
python api_server_v1.py
```

### Custom Configuration

```bash
# Use different LLM provider
python api_server_v1.py llm=qwen-3 llm.base_url=http://localhost:61002/v1

# Custom host and port via environment variables
export API_HOST=127.0.0.1
export API_PORT=8080
python api_server_v1.py
```

## Quick Test

### 1. Health Check

```bash
curl http://localhost:8000/health
# Expected: {"status":"healthy"}
```

### 2. Create a Plan

```bash
curl -X POST http://localhost:8000/v1/api/plan \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: my-session-123" \
  -d '{
    "query": "What is 2+2?",
    "history": []
  }'
```

Expected output (streaming):
```json
{"type":"start","step":1,"delta":""}
{"type":"plan","step":1,"delta":"Workflow "}
{"type":"plan","step":1,"delta":"started"}
{"type":"end","step":1,"delta":""}
...
```

### 3. Execute a Plan

```bash
curl -X POST http://localhost:8000/v1/api/execute \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: my-session-123" \
  -d '{
    "plan": [
      {"type":"plan","step":1,"content":"Calculate 2+2"},
      {"type":"plan","step":2,"content":"Return the result"}
    ]
  }'
```

Expected output (streaming):
```json
{"type":"start","step":1,"delta":""}
{"type":"answer","step":1,"delta":"2+2"}
{"type":"answer","step":1,"delta":" equals"}
{"type":"answer","step":1,"delta":" 4"}
{"type":"end","step":1,"delta":""}
...
```

### 4. Upload a File

```bash
# Create a test file
echo "Hello World" > /tmp/test.txt

# Upload to sandbox
curl -X POST http://localhost:8000/v1/api/upload \
  -H "X-Session-Id: my-session-123" \
  -F "file=@/tmp/test.txt"
```

Expected output:
```json
{"status":"success","message":"File uploaded successfully"}
```

## Python Client Example

```python
import requests
import json

# Configuration
BASE_URL = "http://localhost:8000"
SESSION_ID = "my-session-123"

# 1. Create a plan
def get_plan(query):
    url = f"{BASE_URL}/v1/api/plan"
    headers = {
        "Content-Type": "application/json",
        "X-Session-Id": SESSION_ID
    }
    data = {
        "query": query,
        "history": []
    }
    
    response = requests.post(url, headers=headers, json=data, stream=True)
    
    plan_steps = []
    for line in response.iter_lines():
        if line:
            event = json.loads(line)
            if event["type"] == "plan":
                plan_steps.append(event)
                print(f"[Plan Step {event['step']}] {event['delta']}", end="", flush=True)
    
    print()  # New line after streaming
    return plan_steps

# 2. Execute a plan
def execute_plan(plan):
    url = f"{BASE_URL}/v1/api/execute"
    headers = {
        "Content-Type": "application/json",
        "X-Session-Id": SESSION_ID
    }
    
    # Convert plan steps to proper format
    plan_messages = [
        {"type": "plan", "step": i+1, "content": step.get("delta", "")}
        for i, step in enumerate(plan)
    ]
    
    data = {"plan": plan_messages}
    
    response = requests.post(url, headers=headers, json=data, stream=True)
    
    for line in response.iter_lines():
        if line:
            event = json.loads(line)
            if event["type"] == "answer":
                print(event["delta"], end="", flush=True)
    
    print()  # New line after streaming

# 3. Upload a file
def upload_file(file_path):
    url = f"{BASE_URL}/v1/api/upload"
    headers = {"X-Session-Id": SESSION_ID}
    
    with open(file_path, "rb") as f:
        files = {"file": f}
        response = requests.post(url, headers=headers, files=files)
        return response.json()

# Example usage
if __name__ == "__main__":
    # Get a plan
    query = "What is 2+2?"
    print(f"Query: {query}")
    print("Creating plan...")
    plan = get_plan(query)
    
    print("\nExecuting plan...")
    execute_plan(plan)
    
    # Upload a file
    print("\nUploading file...")
    result = upload_file("/tmp/test.txt")
    print(result)
```

## Environment Variables

Configure the server behavior:

```bash
# Server configuration
export API_HOST=0.0.0.0          # Listen address (default: 0.0.0.0)
export API_PORT=8000             # Listen port (default: 8000)

# LLM configuration (or use Hydra overrides)
export OPENAI_API_KEY=your-key
export ANTHROPIC_API_KEY=your-key
```

## Troubleshooting

### Server won't start
- Check that all dependencies are installed: `pip install -e .`
- Check that port 8000 is available: `lsof -i :8000`
- Check configuration files in `conf/` directory

### Sandbox errors
- Sandboxes expire after 3600 seconds (1 hour)
- Each session ID gets its own sandbox
- Sandbox is automatically recreated if expired

### Streaming not working
- Make sure you're using `stream=True` in requests
- Process response line-by-line with `iter_lines()`
- Check that Content-Type is set correctly

## Next Steps

- Read the full documentation: [API_V1_README.md](./API_V1_README.md)
- Explore the original API: [API_README.md](./API_README.md)
- Check the test suite: `python test_api_v1_structure.py`

## Support

For issues or questions:
- Check the documentation in `API_V1_README.md`
- Review the code in `api_server_v1.py`
- Run structure tests: `python test_api_v1_structure.py`
