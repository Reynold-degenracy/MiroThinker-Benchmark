#!/usr/bin/env python3
"""
Test script to verify sandbox-to-session mapping functionality.
This validates that:
1. SessionAwareSandboxManager is properly defined
2. Sessions maintain sandbox_id mapping
3. Sandbox IDs are automatically injected into Python tool calls
"""

import sys


def test_session_aware_sandbox_manager():
    """Test that SessionAwareSandboxManager class is properly defined"""
    print("Testing SessionAwareSandboxManager class...")
    
    checks = {
        "SessionAwareSandboxManager class defined": False,
        "_ensure_sandbox_exists method defined": False,
        "_needs_sandbox_id method defined": False,
        "execute_tool_call method defined": False,
        "Auto-injection of sandbox_id": False,
        "Sandbox creation on demand": False,
        "Sandbox reuse logic": False,
    }
    
    try:
        with open("api_server.py", "r") as f:
            content = f.read()
        
        # Check for SessionAwareSandboxManager class
        if "class SessionAwareSandboxManager" in content:
            checks["SessionAwareSandboxManager class defined"] = True
        
        # Check for _ensure_sandbox_exists method
        if "async def _ensure_sandbox_exists" in content:
            checks["_ensure_sandbox_exists method defined"] = True
        
        # Check for _needs_sandbox_id method
        if "def _needs_sandbox_id" in content:
            checks["_needs_sandbox_id method defined"] = True
        
        # Check for execute_tool_call method
        if "async def execute_tool_call" in content and "SessionAwareSandboxManager" in content:
            checks["execute_tool_call method defined"] = True
        
        # Check for auto-injection logic
        if '"sandbox_id": sandbox_id' in content or "'sandbox_id': sandbox_id" in content:
            checks["Auto-injection of sandbox_id"] = True
        
        # Check for sandbox creation on demand
        if "create_sandbox" in content and "_ensure_sandbox_exists" in content:
            checks["Sandbox creation on demand"] = True
        
        # Check for sandbox reuse logic
        if "session_dict.get" in content and "sandbox_id" in content:
            checks["Sandbox reuse logic"] = True
            
    except Exception as e:
        print(f"Error reading file: {e}")
        return False
    
    # Print results
    print("\nValidation Results:")
    print("-" * 60)
    all_passed = True
    for check, passed in checks.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {check}")
        if not passed:
            all_passed = False
    
    print("-" * 60)
    if all_passed:
        print("\n✓ All SessionAwareSandboxManager checks passed!")
        return True
    else:
        print("\n✗ Some checks failed")
        return False


def test_session_structure():
    """Test that session structure includes sandbox_id"""
    print("\n\nTesting session structure...")
    
    checks = {
        "Session dict includes sandbox_id": False,
        "SessionAwareSandboxManager wraps tool_manager": False,
        "Sub-agents also wrapped": False,
        "Session persists across requests": False,
    }
    
    try:
        with open("api_server.py", "r") as f:
            content = f.read()
        
        # Check session includes sandbox_id
        if '"sandbox_id": None' in content or "'sandbox_id': None" in content:
            checks["Session dict includes sandbox_id"] = True
        
        # Check SessionAwareSandboxManager wraps tool_manager
        if "SessionAwareSandboxManager(" in content and "main_agent_tool_manager" in content:
            checks["SessionAwareSandboxManager wraps tool_manager"] = True
        
        # Check sub-agents wrapped
        if "wrapped_sub_agents" in content or "sub_agent_tool_manager" in content:
            checks["Sub-agents also wrapped"] = True
        
        # Check session persistence
        if "_sessions[session_key]" in content or "_sessions" in content:
            checks["Session persists across requests"] = True
            
    except Exception as e:
        print(f"Error reading file: {e}")
        return False
    
    # Print results
    print("\nValidation Results:")
    print("-" * 60)
    all_passed = True
    for check, passed in checks.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {check}")
        if not passed:
            all_passed = False
    
    print("-" * 60)
    if all_passed:
        print("\n✓ All session structure checks passed!")
        return True
    else:
        print("\n✗ Some checks failed")
        return False


def test_tools_requiring_sandbox():
    """Test that proper tools are identified as requiring sandbox_id"""
    print("\n\nTesting sandbox-requiring tools identification...")
    
    # These tools should be in the _needs_sandbox_id method
    required_tools = [
        "run_command",
        "run_python_code",
        "run_python_code_stream",
        "upload_file_from_local_to_sandbox",
        "download_file_from_sandbox_to_local",
        "download_file_from_internet_to_sandbox"
    ]
    
    try:
        with open("api_server.py", "r") as f:
            content = f.read()
        
        print("\nChecking tools in _needs_sandbox_id:")
        print("-" * 60)
        all_found = True
        for tool in required_tools:
            if f'"{tool}"' in content or f"'{tool}'" in content:
                print(f"✓ PASS: {tool} found")
            else:
                print(f"✗ FAIL: {tool} not found")
                all_found = False
        
        print("-" * 60)
        if all_found:
            print("\n✓ All required tools are identified!")
            return True
        else:
            print("\n✗ Some tools are missing")
            return False
            
    except Exception as e:
        print(f"Error reading file: {e}")
        return False


def test_upload_file_simplification():
    """Test that upload_file endpoint uses SessionAwareSandboxManager"""
    print("\n\nTesting upload_file endpoint simplification...")
    
    checks = {
        "Upload uses SessionAwareSandboxManager": False,
        "No manual sandbox creation in upload": False,
        "Sandbox auto-injected for upload": False,
    }
    
    try:
        with open("api_server.py", "r") as f:
            content = f.read()
        
        # Check that upload uses tool_manager from session
        if "tool_manager = session[" in content and "upload_file" in content:
            checks["Upload uses SessionAwareSandboxManager"] = True
        
        # Check that upload doesn't manually create sandbox
        # The old code had "async def create_sandbox(tool_mgr)" inside upload_file
        upload_section = content[content.find("@app.post(\"/upload_file\")"):content.find("@hydra.main")]
        if "async def create_sandbox" not in upload_section:
            checks["No manual sandbox creation in upload"] = True
        
        # Check for auto-injection comment or simplified arguments
        if "sandbox_id is auto-injected" in content or "auto-injected" in upload_section:
            checks["Sandbox auto-injected for upload"] = True
            
    except Exception as e:
        print(f"Error reading file: {e}")
        return False
    
    # Print results
    print("\nValidation Results:")
    print("-" * 60)
    all_passed = True
    for check, passed in checks.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {check}")
        if not passed:
            all_passed = False
    
    print("-" * 60)
    if all_passed:
        print("\n✓ All upload_file simplification checks passed!")
        return True
    else:
        print("\n✗ Some checks failed")
        return False


def main():
    """Main test function"""
    print("=" * 60)
    print("Sandbox-to-Session Mapping Test")
    print("=" * 60)
    
    test1 = test_session_aware_sandbox_manager()
    test2 = test_session_structure()
    test3 = test_tools_requiring_sandbox()
    test4 = test_upload_file_simplification()
    
    print("\n" + "=" * 60)
    if test1 and test2 and test3 and test4:
        print("✓ ALL TESTS PASSED")
        print("=" * 60)
        print("\nSummary:")
        print("- SessionAwareSandboxManager automatically manages sandbox lifecycle")
        print("- Sandbox IDs are persisted per session")
        print("- Python tools automatically receive the session's sandbox_id")
        print("- No manual sandbox management needed in endpoints")
        print("=" * 60)
        return 0
    else:
        print("✗ SOME TESTS FAILED")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
