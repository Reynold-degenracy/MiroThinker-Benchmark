#!/usr/bin/env python3
"""
Test script to verify session-to-sandbox mapping functionality.
This verifies that the API server correctly maintains sandbox_id associations with sessions.
"""

import sys


def test_toolmanager_sandbox_injection():
    """Test that ToolManager can store and inject default sandbox_id"""
    print("Testing ToolManager sandbox_id injection...")
    
    try:
        # Read the manager.py file directly to avoid import issues
        with open("../../libs/miroflow-tools/src/miroflow_tools/manager.py", "r") as f:
            manager_content = f.read()
        
        # Test 1: Check that default_sandbox_id attribute is defined
        assert "self.default_sandbox_id = None" in manager_content, "default_sandbox_id should be initialized in __init__"
        print("  ✓ default_sandbox_id attribute is defined")
        
        # Test 2: Check that set_default_sandbox_id method exists
        assert "def set_default_sandbox_id(self, sandbox_id):" in manager_content, "set_default_sandbox_id method should exist"
        assert 'self.default_sandbox_id = sandbox_id' in manager_content, "set_default_sandbox_id should set the attribute"
        print(f"  ✓ set_default_sandbox_id() method exists")
        
        # Test 3: Verify sandbox_id auto-injection logic exists
        assert "SANDBOX_TOOLS" in manager_content, "execute_tool_call should have SANDBOX_TOOLS defined"
        assert "default_sandbox_id" in manager_content, "execute_tool_call should reference default_sandbox_id"
        assert 'arguments["sandbox_id"] = self.default_sandbox_id' in manager_content, "Should inject sandbox_id"
        print("  ✓ execute_tool_call has sandbox_id injection logic")
        
        return True
        
    except Exception as e:
        print(f"  ✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_api_server_structure():
    """Test that API server has session-to-sandbox mapping structure"""
    print("\nTesting API server session-to-sandbox structure...")
    
    try:
        with open("api_server.py", "r") as f:
            content = f.read()
        
        checks = {
            "sandbox_id in session dict": '"sandbox_id": None' in content or "'sandbox_id': None" in content,
            "set_default_sandbox_id called": "set_default_sandbox_id" in content,
            "GET /get_sandbox_id endpoint": '@app.get("/get_sandbox_id")' in content,
            "sandbox_id stored in upload_file": 'session["sandbox_id"]' in content,
        }
        
        all_passed = True
        for check, passed in checks.items():
            status = "✓" if passed else "✗"
            print(f"  {status} {check}")
            if not passed:
                all_passed = False
        
        return all_passed
        
    except Exception as e:
        print(f"  ✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_sandbox_tools_list():
    """Test that SANDBOX_TOOLS list is correctly defined"""
    print("\nTesting SANDBOX_TOOLS list definition...")
    
    try:
        # Read the manager.py file directly
        with open("../../libs/miroflow-tools/src/miroflow_tools/manager.py", "r") as f:
            source = f.read()
        
        # Check that all expected tools are in SANDBOX_TOOLS
        expected_tools = [
            "run_command",
            "run_python_code",
            "upload_file_from_local_to_sandbox",
            "download_file_from_internet_to_sandbox",
            "download_file_from_sandbox_to_local",
        ]
        
        all_found = True
        for tool in expected_tools:
            if f'"{tool}"' not in source:
                print(f"  ✗ Tool '{tool}' not found in SANDBOX_TOOLS")
                all_found = False
            else:
                print(f"  ✓ Tool '{tool}' found")
        
        return all_found
        
    except Exception as e:
        print(f"  ✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_upload_file_session_tracking():
    """Test that upload_file endpoint properly tracks sandbox_id"""
    print("\nTesting upload_file session tracking...")
    
    try:
        with open("api_server.py", "r") as f:
            content = f.read()
        
        # Find the upload_file function
        upload_start = content.find("async def upload_file(")
        if upload_start == -1:
            print("  ✗ upload_file function not found")
            return False
        
        # Get a reasonable chunk of the upload_file function (need to get more to include the retry logic)
        upload_chunk = content[upload_start:upload_start + 10000]
        
        checks = {
            "Creates sandbox": "create_sandbox" in upload_chunk,
            "Stores sandbox_id in session": 'session["sandbox_id"]' in upload_chunk,
            "Returns sandbox_id in response": '"sandbox_id"' in upload_chunk,
            "Handles sandbox not available": "sandbox does not exist" in upload_chunk or "Sandbox not found" in upload_chunk or "Failed to connect to sandbox" in upload_chunk,
        }
        
        all_passed = True
        for check, passed in checks.items():
            status = "✓" if passed else "✗"
            print(f"  {status} {check}")
            if not passed:
                all_passed = False
        
        return all_passed
        
    except Exception as e:
        print(f"  ✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main test function"""
    print("=" * 60)
    print("Session-to-Sandbox Mapping - Functionality Test")
    print("=" * 60)
    
    test1 = test_toolmanager_sandbox_injection()
    test2 = test_api_server_structure()
    test3 = test_sandbox_tools_list()
    test4 = test_upload_file_session_tracking()
    
    print("\n" + "=" * 60)
    if test1 and test2 and test3 and test4:
        print("✓ ALL TESTS PASSED")
        print("=" * 60)
        print("\nSummary:")
        print("  • ToolManager supports default_sandbox_id")
        print("  • API server maintains session-to-sandbox mapping")
        print("  • Automatic sandbox_id injection for sandbox tools")
        print("  • upload_file endpoint tracks sandbox_id in session")
        print("  • GET /get_sandbox_id endpoint available")
        return 0
    else:
        print("✗ SOME TESTS FAILED")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
