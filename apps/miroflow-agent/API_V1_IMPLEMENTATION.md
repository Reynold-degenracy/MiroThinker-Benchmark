# API v1 Implementation Summary

## Overview

This document summarizes the changes made to implement the new API v1 specification with updated endpoints and message formats.

## Changes Made

### 1. New API Structure

Created a new `api/` directory with the main API implementation:

- `api/__init__.py` - Package initialization
- `api/main.py` - FastAPI application with new endpoints

### 2. New Endpoints

Replaced the legacy `/get_response` endpoint with three new endpoints:

| Old Endpoint | New Endpoint | Purpose |
|-------------|--------------|---------|
| POST /get_response (with is_confirmed=false) | POST /v1/api/plan | Submit questions and get step-by-step planning |
| POST /get_response (with is_confirmed=true) | POST /v1/api/execute | Execute a plan and get results |
| POST /upload_file | POST /v1/api/upload | Upload files to workspace |
| GET /health | GET /health | Health check (unchanged) |

### 3. New Message Format

**Legacy Format:**
```json
{"status": "plan", "step": 1, "data": "..."}
{"status": "answer", "data": "..."}
```

**New Format:**
```json
{"type": "start", "step": 1, "delta": ""}
{"type": "plan", "step": 1, "delta": "..."}
{"type": "end", "step": 1, "delta": ""}
{"type": "answer", "step": 1, "delta": "..."}
```

### 4. Message Types

The new API supports the following message types:

- `query` - User question
- `plan` - Planning step
- `answer` - Answer/response
- `action` - Action taken
- `start` - Step start marker (streaming only)
- `end` - Step end marker (streaming only)

### 5. Request Format Changes

**Legacy /get_response:**
```json
{
  "query": "xxx",
  "history": [...],
  "is_confirmed": false
}
```

**New /v1/api/plan:**
```json
{
  "query": "xxx",
  "history": [
    {"type": "query", "step": -1, "content": "xxx"},
    {"type": "plan", "step": 1, "content": "xxx"}
  ]
}
```

**New /v1/api/execute:**
```json
{
  "plan": [
    {"type": "plan", "step": 1, "content": "xxx"},
    {"type": "plan", "step": 2, "content": "xxx"}
  ]
}
```

### 6. Starting the New API

**New way (recommended):**
```bash
uv run uvicorn api.main:app --host 0.0.0.0 --port 8000
```

**Legacy way:**
```bash
python3 api_server.py
```

### 7. Documentation Updates

- Created `API_v1_README.md` - Comprehensive documentation for the new API
- Updated `API_README.md` - Added note marking it as legacy
- Updated `README.md` - Added instructions for both API versions
- Updated `test_api_structure.py` - Tests now check the new API structure

### 8. Key Features Preserved

- ✓ Session management via `X-Session-Id` header
- ✓ Automatic sandbox creation and management (3600s TTL)
- ✓ Streaming responses in NDJSON format
- ✓ File upload support
- ✓ Stateless server (client maintains history)
- ✓ Hydra configuration support
- ✓ Health check endpoint

### 9. Backward Compatibility

The legacy `api_server.py` remains untouched and functional. Users can choose:

- **New API** (`api/main.py`) - Use the new `/v1/api/*` endpoints with updated message format
- **Legacy API** (`api_server.py`) - Continue using `/get_response` with old message format

## Testing

Run the structure test to verify the new API:

```bash
cd apps/miroflow-agent
python3 test_api_structure.py
```

Expected output:
```
✓ All structure checks passed!
✓ Response format is valid NDJSON
✓ ALL TESTS PASSED
```

## Usage Examples

### Health Check
```bash
curl http://localhost:8000/health
```

### Planning Request
```bash
curl http://localhost:8000/v1/api/plan \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: sess_001" \
  -d '{"query": "What is 2+2?", "history": []}'
```

### Execute Request
```bash
curl http://localhost:8000/v1/api/execute \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: sess_001" \
  -d '{
    "plan": [
      {"type": "plan", "step": 1, "content": "Calculate 2+2"}
    ]
  }'
```

### File Upload
```bash
curl http://localhost:8000/v1/api/upload \
  -H "X-Session-Id: sess_001" \
  -F "file=@/path/to/file.txt"
```

## Migration Guide

For users migrating from the legacy API to v1:

1. **Update endpoint URLs:**
   - `/get_response` → `/v1/api/plan` (for planning) or `/v1/api/execute` (for execution)
   - `/upload_file` → `/v1/api/upload`

2. **Update request format:**
   - Split logic: Use `/v1/api/plan` for planning phase, `/v1/api/execute` for execution
   - Remove `is_confirmed` field from requests
   - Update history format to use `type`, `step`, `content` fields

3. **Update response parsing:**
   - Change from `status` to `type`
   - Change from `data` to `delta`
   - Add support for `start` and `end` message types

4. **Update startup command:**
   ```bash
   # Old
   python3 api_server.py
   
   # New
   uv run uvicorn api.main:app --host 0.0.0.0 --port 8000
   ```

## Implementation Details

### Core Components

1. **SessionAwareSandboxManager**: Unchanged - handles automatic sandbox creation and management
2. **Message Models**: New Pydantic models for `Message`, `PlanRequest`, and `ExecuteRequest`
3. **Stream Generator**: Updated to output new message format with `type`, `step`, `delta` fields
4. **Endpoints**: Three new endpoints that separate planning and execution concerns

### Technical Decisions

1. **Kept sandbox management unchanged**: The SessionAwareSandboxManager logic is reused
2. **Separate planning and execution**: Split into two endpoints for clearer API semantics
3. **Backward compatible**: Legacy API remains available
4. **Streaming format**: Maintained NDJSON streaming for consistency

## Files Changed

- ✓ Created `api/__init__.py`
- ✓ Created `api/main.py`
- ✓ Created `API_v1_README.md`
- ✓ Updated `test_api_structure.py`
- ✓ Updated `API_README.md`
- ✓ Updated `README.md`

## Files Unchanged

- ✓ `api_server.py` - Legacy API remains functional
- ✓ `src/` - Core logic unchanged
- ✓ All other configuration and source files
