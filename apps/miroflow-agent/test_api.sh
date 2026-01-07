#!/bin/bash
# Test script for the MiroFlow Agent API
# This script demonstrates how to call the /get_response endpoint

# Configuration
HOST="localhost"
PORT="8000"
BASE_URL="http://${HOST}:${PORT}"

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

# Test 2: Simple query without history
echo -e "${YELLOW}Test 2: Simple Query (is_confirmed=false)${NC}"
echo "POST ${BASE_URL}/get_response"
echo "Session ID: sess_test_001"
echo ""
curl -N "${BASE_URL}/get_response" \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: sess_test_001" \
  -d '{
    "query": "What is 2 + 2?",
    "history": null,
    "is_confirmed": false
  }' 2>&1

echo ""
echo ""

# Test 3: Query with history
echo -e "${YELLOW}Test 3: Query with History${NC}"
echo "POST ${BASE_URL}/get_response"
echo "Session ID: sess_test_002"
echo ""
curl -N "${BASE_URL}/get_response" \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: sess_test_002" \
  -d '{
    "query": "帮我找到马斯克的最近一条推特",
    "history": [
      {"role": "user", "content": "你好"},
      {"role": "assistant", "content": "你好，有什么可以帮你? "}
    ],
    "is_confirmed": false
  }' 2>&1

echo ""
echo ""

# Test 4: File Upload
echo -e "${YELLOW}Test 4: File Upload${NC}"
echo "POST ${BASE_URL}/upload_file"
echo "Session ID: sess_test_003"
echo ""

# Create a temporary test file
TEST_FILE="/tmp/test_upload.txt"
echo "This is a test file for upload." > "$TEST_FILE"
echo "Created test file: $TEST_FILE"
echo ""

curl "${BASE_URL}/upload_file" \
  -H "X-Session-Id: sess_test_003" \
  -F "file=@${TEST_FILE}" 2>&1

echo ""
echo ""

# Cleanup
rm -f "$TEST_FILE"
echo "Test file cleaned up."
echo ""
