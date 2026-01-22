#!/usr/bin/env python3
"""
Test script to verify API v1 server structure and endpoint definitions.
This performs basic validation without requiring all dependencies.
"""

import json
import sys


def test_api_v1_structure():
    """Test that the API v1 server has the correct structure"""
    print("Testing API v1 server structure...")
    
    # Check if basic FastAPI components are correct
    checks = {
        "FastAPI app defined": False,
        "Message model defined": False,
        "PlanRequest model defined": False,
        "ExecuteRequest model defined": False,
        "GET /health endpoint defined": False,
        "POST /v1/api/plan endpoint defined": False,
        "POST /v1/api/execute endpoint defined": False,
        "POST /v1/api/upload endpoint defined": False,
        "NDJSON streaming used": False,
        "Session management implemented": False,
        "File upload support (UploadFile)": False,
        "X-Session-Id header support": False,
        "Authorization header support": False,
    }
    
    try:
        with open("api_server_v1.py", "r") as f:
            content = f.read()
            
        # Check for FastAPI app
        if "app = FastAPI" in content:
            checks["FastAPI app defined"] = True
            
        # Check for Message model
        if "class Message(BaseModel)" in content and "type: str" in content:
            checks["Message model defined"] = True
            
        # Check for PlanRequest model
        if "class PlanRequest(BaseModel)" in content and "query: str" in content:
            checks["PlanRequest model defined"] = True
            
        # Check for ExecuteRequest model
        if "class ExecuteRequest(BaseModel)" in content and "plan: List[Message]" in content:
            checks["ExecuteRequest model defined"] = True
            
        # Check for health endpoint
        if '@app.get("/health")' in content:
            checks["GET /health endpoint defined"] = True
            
        # Check for plan endpoint
        if '@app.post("/v1/api/plan")' in content:
            checks["POST /v1/api/plan endpoint defined"] = True
            
        # Check for execute endpoint
        if '@app.post("/v1/api/execute")' in content:
            checks["POST /v1/api/execute endpoint defined"] = True
            
        # Check for upload endpoint
        if '@app.post("/v1/api/upload")' in content:
            checks["POST /v1/api/upload endpoint defined"] = True
            
        # Check for NDJSON streaming
        if '"application/x-ndjson"' in content or 'application/x-ndjson' in content:
            checks["NDJSON streaming used"] = True
            
        # Check for session management
        if "X-Session-Id" in content and "_sessions" in content:
            checks["Session management implemented"] = True
            
        # Check for file upload support
        if "UploadFile" in content and "File" in content:
            checks["File upload support (UploadFile)"] = True
            
        # Check for X-Session-Id header
        if 'alias="X-Session-Id"' in content:
            checks["X-Session-Id header support"] = True
            
        # Check for Authorization header
        if 'alias="Authorization"' in content:
            checks["Authorization header support"] = True
            
    except Exception as e:
        print(f"Error reading file: {e}")
        return False
    
    # Print results
    print("\nValidation Results:")
    print("-" * 50)
    all_passed = True
    for check, passed in checks.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {check}")
        if not passed:
            all_passed = False
    
    print("-" * 50)
    if all_passed:
        print("\n✓ All structure checks passed!")
        return True
    else:
        print("\n✗ Some checks failed")
        return False


def test_message_format():
    """Verify message format examples"""
    print("\n\nTesting message format examples...")
    
    # Example messages for streaming responses
    examples = [
        {"type": "start", "step": 1, "delta": ""},
        {"type": "plan", "step": 1, "delta": "xx"},
        {"type": "end", "step": 1, "delta": ""},
        {"type": "start", "step": 2, "delta": ""},
        {"type": "answer", "step": 2, "delta": "x"},
        {"type": "end", "step": 2, "delta": ""},
    ]
    
    print("\nExpected v1 message format (NDJSON):")
    print("-" * 50)
    for example in examples:
        # Verify each example is valid JSON
        try:
            json_str = json.dumps(example)
            print(json_str)
            # Verify it can be parsed back
            parsed = json.loads(json_str)
            assert "type" in parsed
            assert parsed["type"] in ["start", "end", "plan", "answer", "query", "action"]
            assert "step" in parsed
            if "delta" in parsed:
                assert isinstance(parsed["delta"], str)
        except Exception as e:
            print(f"✗ Invalid format: {e}")
            return False
    
    print("-" * 50)
    print("✓ Message format is valid NDJSON")
    return True


def test_request_models():
    """Verify request model examples"""
    print("\n\nTesting request model examples...")
    
    # Example plan request
    plan_request = {
        "query": "xxx",
        "history": [
            {"type": "query", "step": -1, "content": "xxx"},
            {"type": "plan", "step": 1, "content": "xxx"},
        ]
    }
    
    # Example execute request
    execute_request = {
        "plan": [
            {"type": "plan", "step": 1, "content": "xxx"},
            {"type": "plan", "step": 2, "content": "xxx"},
        ]
    }
    
    print("\nExpected plan request format:")
    print("-" * 50)
    try:
        json_str = json.dumps(plan_request, indent=2)
        print(json_str)
        parsed = json.loads(json_str)
        assert "query" in parsed
        assert "history" in parsed
        print("✓ Plan request format is valid")
    except Exception as e:
        print(f"✗ Invalid plan request format: {e}")
        return False
    
    print("\nExpected execute request format:")
    print("-" * 50)
    try:
        json_str = json.dumps(execute_request, indent=2)
        print(json_str)
        parsed = json.loads(json_str)
        assert "plan" in parsed
        print("✓ Execute request format is valid")
    except Exception as e:
        print(f"✗ Invalid execute request format: {e}")
        return False
    
    print("-" * 50)
    return True


def main():
    """Main test function"""
    print("=" * 50)
    print("MiroFlow Agent API v1 - Structure Test")
    print("=" * 50)
    
    test1 = test_api_v1_structure()
    test2 = test_message_format()
    test3 = test_request_models()
    
    print("\n" + "=" * 50)
    if test1 and test2 and test3:
        print("✓ ALL TESTS PASSED")
        print("=" * 50)
        return 0
    else:
        print("✗ SOME TESTS FAILED")
        print("=" * 50)
        return 1


if __name__ == "__main__":
    sys.exit(main())
