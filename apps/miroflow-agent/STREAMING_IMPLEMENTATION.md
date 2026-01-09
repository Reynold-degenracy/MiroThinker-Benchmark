# Streaming Implementation Documentation

This document describes the streaming output implementation for the MiroFlow Agent.

## Overview

The MiroFlow Agent now supports real-time streaming output from LLM API calls all the way to the client. This enables users to see responses as they are generated, rather than waiting for the complete response.

## Architecture

The streaming implementation follows a queue-based architecture:

```
Client → API Server → Orchestrator → LLM Client → LLM API
         (stream_queue) ↑           ↑             ↑
                        └───────────┴─────────────┘
                           Streaming Updates
```

### Components

1. **API Server (`api_server.py`)**
   - Creates an `asyncio.Queue` (stream_queue) for each request
   - Passes stream_queue through the pipeline
   - Consumes events from the queue and transforms them to NDJSON format
   - Streams responses to the client using FastAPI's `StreamingResponse`

2. **Orchestrator (`src/core/orchestrator.py`)**
   - Receives stream_queue from the pipeline
   - Passes it to LLM client when calling `create_message`
   - Can also send custom streaming events (e.g., tool_call, start_of_agent)

3. **LLM Clients (`src/llm/providers/`)**
   - **OpenAI Client**: Handles OpenAI-compatible streaming responses
   - **Anthropic Client**: Handles Anthropic-specific streaming events
   - Both clients:
     - Enable `stream=True` in API calls
     - Process streaming chunks/events in real-time
     - Send deltas to stream_queue
     - Accumulate complete response for history and token counting

## Implementation Details

### OpenAI Client Streaming

The OpenAI client supports streaming through the `stream=True` parameter:

```python
# Streaming is enabled in API calls
params = {
    "model": self.model_name,
    "messages": messages_for_llm,
    "stream": True,  # Enable streaming
    ...
}
```

Two methods handle streaming:
- `_handle_streaming_response`: For async clients
- `_handle_sync_streaming_response`: For sync clients

Both methods:
1. Iterate through streaming chunks
2. Extract content deltas and tool calls
3. Send deltas to stream_queue
4. Accumulate complete response
5. Return a mock response object compatible with non-streaming format

### Anthropic Client Streaming

The Anthropic client handles Anthropic-specific streaming events:

```python
# Different event types
- message_start: Initial message with usage data
- content_block_start: Start of a content block (text or tool_use)
- content_block_delta: Content deltas (text_delta or input_json_delta)
- message_delta: Message-level updates (stop_reason, usage)
- message_stop: End of streaming
```

Like the OpenAI client, it provides both async and sync streaming handlers.

### Stream Queue Events

Events sent through stream_queue follow this format:

```python
{
    "event": "message",  # or "tool_call", "start_of_agent", etc.
    "data": {
        "message_id": "uuid",
        "delta": {
            "content": "text fragment"
        }
    }
}
```

### API Response Format

The API server transforms stream events to NDJSON format:

```json
{"status":"plan", "step": 1, "data":"Workflow started"}
{"status":"answer", "data":"I am"}
{"status":"answer", "data":" streaming"}
{"status":"answer", "data":"!\n"}
```

## Benefits

1. **Real-time Feedback**: Users see responses as they're generated
2. **Better UX**: No waiting for long responses to complete
3. **Efficient**: Streaming reduces perceived latency
4. **Backward Compatible**: Complete responses are still accumulated for history
5. **Dual Mode Support**: Works with both async and sync LLM clients

## Testing

To test streaming:

1. Start the API server:
```bash
cd apps/miroflow-agent
python3 api_server.py
```

2. Use curl to test streaming:
```bash
curl -N http://localhost:8000/get_response \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: test_session" \
  -d '{
    "query": "What is 2+2?",
    "is_confirmed": false
  }'
```

The `-N` flag disables curl's buffering, allowing you to see streamed responses in real-time.

## Configuration

Streaming is enabled by default and requires no configuration changes. The implementation automatically:
- Detects whether to use async or sync streaming based on `async_client` setting
- Handles both OpenAI-compatible and Anthropic APIs
- Maintains token counting and usage tracking
- Preserves complete message history

## Compatibility

- **OpenAI-compatible APIs**: ✅ Supported
- **Anthropic API**: ✅ Supported  
- **Async clients**: ✅ Supported
- **Sync clients**: ✅ Supported
- **Token counting**: ✅ Preserved
- **Message history**: ✅ Preserved
- **Tool calls**: ✅ Supported

## Future Improvements

1. Extract duplicate mock classes to reduce code duplication
2. Add streaming progress indicators for long-running tool executions
3. Implement streaming timeouts and error recovery
4. Add metrics for streaming performance monitoring

## Troubleshooting

### No streaming output

**Problem**: Responses appear all at once instead of streaming

**Solutions**:
1. Ensure client is using `-N` flag with curl or equivalent for HTTP clients
2. Check that `stream_queue` is being passed through the pipeline
3. Verify LLM API supports streaming (check API documentation)

### Incomplete responses

**Problem**: Streaming stops before complete response

**Solutions**:
1. Check network connectivity
2. Verify API rate limits are not exceeded
3. Review server logs for errors
4. Ensure timeout settings are appropriate

### Token counting mismatch

**Problem**: Token counts don't match expected values

**Solutions**:
1. Verify usage data is captured in final streaming chunk
2. Check that `_update_token_usage` is called after streaming completes
3. Review logs for usage data extraction

## Code References

- API Server: `apps/miroflow-agent/api_server.py`
- OpenAI Client: `apps/miroflow-agent/src/llm/providers/openai_client.py`
- Anthropic Client: `apps/miroflow-agent/src/llm/providers/anthropic_client.py`
- Base Client: `apps/miroflow-agent/src/llm/base_client.py`
- Orchestrator: `apps/miroflow-agent/src/core/orchestrator.py`
