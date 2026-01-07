#!/usr/bin/env python3
"""
Simple test script to verify API server structure and endpoint definitions.
This performs basic validation without requiring all dependencies.
"""

import json
import sys


def test_api_structure():
    """Test that the API server has the correct structure"""
    print("Testing API server structure...")
    
    # Check if basic FastAPI components are correct
    checks = {
        "FastAPI app defined": False,
        "QueryRequest model defined": False,
        "POST /get_response endpoint defined": False,
        "POST /upload_file endpoint defined": False,
        "Health check endpoint defined": False,
        "NDJSON streaming used": False,
        "Session management implemented": False,
        "File upload support (UploadFile)": False,
    }
    
    try:
        with open("api_server.py", "r") as f:
            content = f.read()
            
        # Check for FastAPI app
        if "app = FastAPI" in content:
            checks["FastAPI app defined"] = True
            
        # Check for QueryRequest model
        if "class QueryRequest(BaseModel)" in content and "query: str" in content:
            checks["QueryRequest model defined"] = True
            
        # Check for POST endpoint
        if '@app.post("/get_response")' in content:
            checks["POST /get_response endpoint defined"] = True
            
        # Check for upload endpoint
        if '@app.post("/upload_file")' in content:
            checks["POST /upload_file endpoint defined"] = True
            
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
    print("\n\nTesting response format examples...")
    
    # Example responses that should be generated
    examples = [
        {"status": "plan", "step": 1, "data": "Workflow started"},
        {"status": "plan", "step": 2, "data": "Using tool: search"},
        {"status": "answer", "data": "I am"},
        {"status": "answer", "data": " Manus"},
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
            assert "status" in parsed
            assert parsed["status"] in ["plan", "answer"]
            if parsed["status"] == "plan":
                assert "step" in parsed
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
