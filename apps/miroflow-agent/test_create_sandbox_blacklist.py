#!/usr/bin/env python3
"""
Test to verify that create_sandbox tool is automatically blacklisted
when using SessionAwareSandboxManager.
"""

import sys
sys.path.insert(0, '.')

def test_create_sandbox_blacklisted():
    """Test that create_sandbox is in the tool blacklist"""
    print("=" * 60)
    print("Testing create_sandbox Auto-Blacklisting")
    print("=" * 60)
    
    # Check if api_server properly blacklists create_sandbox
    with open("api_server.py", "r") as f:
        content = f.read()
    
    checks = {
        "Blacklist create_sandbox in code": False,
        "Log message about auto-blacklisting": False,
        "Applied to main agent": False,
        "Applied to sub-agents": False,
    }
    
    # Check for blacklist addition
    if 'tool_blacklist.add(("tool-python", "create_sandbox"))' in content:
        checks["Blacklist create_sandbox in code"] = True
    
    # Check for log message
    if "Auto-blacklisted 'create_sandbox'" in content or "auto-blacklist" in content.lower():
        checks["Log message about auto-blacklisting"] = True
    
    # Check it's applied to main agent
    if "main_agent_tool_manager.tool_blacklist" in content:
        checks["Applied to main agent"] = True
    
    # Check it's applied to sub-agents
    if "sub_agent_tool_manager.tool_blacklist" in content:
        checks["Applied to sub-agents"] = True
    
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
        print("\n✓ All blacklist checks passed!")
        print("\nExplanation:")
        print("- create_sandbox is automatically hidden from the LLM")
        print("- LLM cannot directly call create_sandbox")
        print("- SessionAwareSandboxManager handles sandbox creation internally")
        print("- This prevents the LLM from overwriting existing sandboxes")
        return True
    else:
        print("\n✗ Some checks failed")
        return False


if __name__ == "__main__":
    success = test_create_sandbox_blacklisted()
    sys.exit(0 if success else 1)
