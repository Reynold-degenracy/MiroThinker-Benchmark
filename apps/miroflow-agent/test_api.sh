#!/bin/bash
# Test script for the MiroFlow Agent API
# This script demonstrates how to call the execute and upload endpoints

# Configuration
HOST="localhost"
PORT="7210"
BASE_URL="http://${HOST}:${PORT}"
SESSION_ID="sess_$(date +%s)"
AUTH_TOKEN="Bearer test_token_xyz"

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=========================================="
echo "MiroFlow Agent API Test Script"
echo "=========================================="
echo ""

# Test 1: Health check
echo -e "${YELLOW}Test 1: Health Check${NC}"
echo "GET ${BASE_URL}/health"
echo ""
response=$(curl -s "${BASE_URL}/health" 2>&1)
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Response:${NC} $response"
else
    echo -e "${RED}✗ Failed to connect to server${NC}"
    echo "Please make sure the server is running with: python3 api_server.py"
    exit 1
fi
echo ""

# # Test 2: Execute query (streaming response)
# echo -e "${YELLOW}Test 2: Execute Query (Streaming Response)${NC}"
# echo "POST ${BASE_URL}/v1/api/execute"
# echo "Session ID: $SESSION_ID"
# echo "Authorization: $AUTH_TOKEN"
# echo ""
# curl -N "${BASE_URL}/v1/api/execute" \
#   -H "Content-Type: application/json" \
#   -H "X-Session-Id: $SESSION_ID" \
#   -H "Authorization: $AUTH_TOKEN" \
#   -d '{
#     "message": [
#       {
#         "type": "query",
#         "step": 1,
#         "content": "What is 2 + 2?"
#       }
#     ]
#   }' 2>&1

# echo ""
# echo ""

# Test 3: Execute another query with different content
echo -e "${YELLOW}Test 3: Execute Another Query${NC}"
echo "POST ${BASE_URL}/v1/api/execute"
echo "Session ID: $SESSION_ID"
echo ""
curl -N "${BASE_URL}/v1/api/execute" \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: $SESSION_ID" \
  -H "Authorization: $AUTH_TOKEN" \
  -d '{
    "message": [
      {
        "type": "query",
        "step": 1,
        "content": "Find the Latest tweet of Elon Musk."
      }
    ]
  }' 2>&1 | python3 -u -c 'import json,sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        continue
    delta = obj.get("delta")
    if delta:
        sys.stdout.write(delta)
        sys.stdout.flush()'

echo ""
echo ""

# # Test 4: File Upload
# echo -e "${YELLOW}Test 4: File Upload${NC}"
# echo "POST ${BASE_URL}/upload"
# echo "Session ID: $SESSION_ID"
# echo "Authorization: $AUTH_TOKEN"
# echo ""

# # Create a temporary test file
# TEST_FILE="/tmp/test_upload_$(date +%s).txt"
# echo "This is a test file for upload." > "$TEST_FILE"
# echo "Created test file: $TEST_FILE"
# echo ""

# curl -s "${BASE_URL}/upload" \
#   -H "X-Session-Id: $SESSION_ID" \
#   -H "Authorization: $AUTH_TOKEN" \
#   -F "file=@${TEST_FILE}" 2>&1

# echo ""
# echo ""

# # Cleanup
# rm -f "$TEST_FILE"
# echo "Test file cleaned up."
# echo ""
