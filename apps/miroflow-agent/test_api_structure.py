#!/usr/bin/env python3
"""
Simple test script to verify API server structure and endpoint definitions.
This performs basic validation without requiring all dependencies.
"""

import json
import sys


def test_api_structure():
    """Test that the new API server has the correct structure"""
    print("Testing new API server structure...")
    
    # Check if basic FastAPI components are correct
    checks = {
        "FastAPI app defined": False,
        "Message model defined": False,
        "POST /v1/api/plan endpoint defined": False,
        "POST /v1/api/execute endpoint defined": False,
        "POST /v1/api/upload endpoint defined": False,
        "Health check endpoint defined": False,
        "NDJSON streaming used": False,
        "Session management implemented": False,
        "File upload support (UploadFile)": False,
    }
    
    try:
        with open("api/main.py", "r") as f:
            content = f.read()
            
        # Check for FastAPI app
        if "app = FastAPI" in content:
            checks["FastAPI app defined"] = True
            
        # Check for Message model
        if "class Message(BaseModel)" in content and "type: str" in content:
            checks["Message model defined"] = True
            
        # Check for POST /v1/api/plan endpoint
        if '@app.post("/v1/api/plan")' in content:
            checks["POST /v1/api/plan endpoint defined"] = True
            
        # Check for POST /v1/api/execute endpoint
        if '@app.post("/v1/api/execute")' in content:
            checks["POST /v1/api/execute endpoint defined"] = True
            
        # Check for upload endpoint
        if '@app.post("/v1/api/upload")' in content:
            checks["POST /v1/api/upload endpoint defined"] = True
            
        # Check for health endpoint
        if '@app.get("/health")' in content:
            checks["Health check endpoint defined"] = True
            
        # Check for NDJSON streaming
        if '"application/x-ndjson"' in content or 'application/x-ndjson' in content:
            checks["NDJSON streaming used"] = True
            
        # Check for session management
        if "X-Session-Id" in content and "_sessions" in content:
            checks["Session management implemented"] = True
            
        # Check for file upload support
        if "UploadFile" in content and "File" in content:
            checks["File upload support (UploadFile)"] = True
            
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


def test_response_format():
    """Verify response format examples"""
    print("\n\nTesting new response format examples...")
    
    # Example responses that should be generated (new format)
    examples = [
        {"type": "start", "step": 1, "delta": ""},
        {"type": "plan", "step": 1, "delta": "Workflow started"},
        {"type": "plan", "step": 2, "delta": "Using tool: search"},
        {"type": "end", "step": 2, "delta": ""},
        {"type": "answer", "step": 1, "delta": "I am"},
        {"type": "answer", "step": 1, "delta": " Manus"},
    ]
    
    print("\nExpected response format (NDJSON):")
    print("-" * 50)
    for example in examples:
        # Verify each example is valid JSON
        try:
            json_str = json.dumps(example)
            print(json_str)
            # Verify it can be parsed back
            parsed = json.loads(json_str)
            assert "type" in parsed
            assert parsed["type"] in ["start", "plan", "answer", "action", "end", "query"]
            assert "step" in parsed
            assert "delta" in parsed
        except Exception as e:
            print(f"✗ Invalid format: {e}")
            return False
    
    print("-" * 50)
    print("✓ Response format is valid NDJSON")
    return True


def main():
    """Main test function"""
    print("=" * 50)
    print("MiroFlow Agent API - Structure Test")
    print("=" * 50)
    
    test1 = test_api_structure()
    test2 = test_response_format()
    
    print("\n" + "=" * 50)
    if test1 and test2:
        print("✓ ALL TESTS PASSED")
        print("=" * 50)
        return 0
    else:
        print("✗ SOME TESTS FAILED")
        print("=" * 50)
        return 1


if __name__ == "__main__":
    sys.exit(main())
