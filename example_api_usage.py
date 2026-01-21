#!/usr/bin/env python3
"""
Example script demonstrating how to use the MiroFlow Agent API.

This script shows how to:
1. Check API health
2. Submit a query to /v1/api/plan
3. Execute a plan with /v1/api/execute
4. Upload a file to /v1/api/upload

Note: This requires the API server to be running:
    uv run uvicorn api.main:app --host 0.0.0.0 --port 8000
"""

import json
import requests
import sys


def check_health(base_url="http://localhost:8000"):
    """Check API health status"""
    print("=" * 60)
    print("Checking API Health")
    print("=" * 60)
    
    try:
        response = requests.get(f"{base_url}/health")
        response.raise_for_status()
        print(f"✅ Health check passed: {response.json()}")
        return True
    except Exception as e:
        print(f"❌ Health check failed: {e}")
        return False


def test_plan_endpoint(base_url="http://localhost:8000", session_id="test_session_001"):
    """Test the /v1/api/plan endpoint"""
    print("\n" + "=" * 60)
    print("Testing /v1/api/plan endpoint")
    print("=" * 60)
    
    headers = {
        "Content-Type": "application/json",
        "X-Session-Id": session_id,
        "Authorization": "Bearer test_token"
    }
    
    payload = {
        "query": "What is 2+2?",
        "history": []
    }
    
    print(f"\nRequest:")
    print(f"  URL: {base_url}/v1/api/plan")
    print(f"  Headers: {headers}")
    print(f"  Payload: {json.dumps(payload, indent=2)}")
    
    try:
        response = requests.post(
            f"{base_url}/v1/api/plan",
            headers=headers,
            json=payload,
            stream=True
        )
        response.raise_for_status()
        
        print(f"\nResponse (streaming):")
        line_count = 0
        for line in response.iter_lines():
            if line:
                line_count += 1
                try:
                    event = json.loads(line)
                    print(f"  {json.dumps(event)}")
                except json.JSONDecodeError as e:
                    print(f"  ⚠️  Invalid JSON: {line.decode('utf-8', errors='ignore')}")
                if line_count >= 10:  # Limit output for demo
                    print("  ... (truncated)")
                    break
        
        print(f"✅ Plan endpoint test passed")
        return True
        
    except Exception as e:
        print(f"❌ Plan endpoint test failed: {e}")
        return False


def test_execute_endpoint(base_url="http://localhost:8000"):
    """Test the /v1/api/execute endpoint"""
    print("\n" + "=" * 60)
    print("Testing /v1/api/execute endpoint")
    print("=" * 60)
    
    headers = {
        "Content-Type": "application/json",
    }
    
    payload = {
        "plan": [
            {"type": "plan", "step": 1, "content": "Calculate 2+2"},
            {"type": "plan", "step": 2, "content": "Return the result"}
        ]
    }
    
    print(f"\nRequest:")
    print(f"  URL: {base_url}/v1/api/execute")
    print(f"  Headers: {headers}")
    print(f"  Payload: {json.dumps(payload, indent=2)}")
    
    try:
        response = requests.post(
            f"{base_url}/v1/api/execute",
            headers=headers,
            json=payload,
            stream=True
        )
        response.raise_for_status()
        
        print(f"\nResponse (streaming):")
        line_count = 0
        for line in response.iter_lines():
            if line:
                line_count += 1
                try:
                    event = json.loads(line)
                    print(f"  {json.dumps(event)}")
                except json.JSONDecodeError as e:
                    print(f"  ⚠️  Invalid JSON: {line.decode('utf-8', errors='ignore')}")
                if line_count >= 10:  # Limit output for demo
                    print("  ... (truncated)")
                    break
        
        print(f"✅ Execute endpoint test passed")
        return True
        
    except Exception as e:
        print(f"❌ Execute endpoint test failed: {e}")
        return False


def test_upload_endpoint(base_url="http://localhost:8000", session_id="test_session_001"):
    """Test the /v1/api/upload endpoint"""
    print("\n" + "=" * 60)
    print("Testing /v1/api/upload endpoint")
    print("=" * 60)
    
    headers = {
        "X-Session-Id": session_id,
        "Authorization": "Bearer test_token"
    }
    
    # Create a temporary test file
    test_file_content = b"This is a test file for upload.\nIt contains multiple lines.\n"
    files = {
        "file": ("test_upload.txt", test_file_content, "text/plain")
    }
    
    print(f"\nRequest:")
    print(f"  URL: {base_url}/v1/api/upload")
    print(f"  Headers: {headers}")
    print(f"  File: test_upload.txt ({len(test_file_content)} bytes)")
    
    try:
        response = requests.post(
            f"{base_url}/v1/api/upload",
            headers=headers,
            files=files
        )
        response.raise_for_status()
        
        result = response.json()
        print(f"\nResponse:")
        print(f"  {json.dumps(result, indent=2)}")
        print(f"✅ Upload endpoint test passed")
        return True
        
    except Exception as e:
        print(f"❌ Upload endpoint test failed: {e}")
        return False


def main():
    """Main function to run all tests"""
    print("\n" + "=" * 60)
    print("MiroFlow Agent API - Example Usage")
    print("=" * 60)
    
    base_url = "http://localhost:8000"
    
    # Check health first
    if not check_health(base_url):
        print("\n⚠️  API server is not running or not responding.")
        print("Please start the server with:")
        print("    uv run uvicorn api.main:app --host 0.0.0.0 --port 8000")
        return 1
    
    # Run tests
    results = []
    results.append(("Plan endpoint", test_plan_endpoint(base_url)))
    results.append(("Execute endpoint", test_execute_endpoint(base_url)))
    results.append(("Upload endpoint", test_upload_endpoint(base_url)))
    
    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {name}")
    
    all_passed = all(passed for _, passed in results)
    print("=" * 60)
    if all_passed:
        print("✅ All tests passed!")
        return 0
    else:
        print("❌ Some tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
