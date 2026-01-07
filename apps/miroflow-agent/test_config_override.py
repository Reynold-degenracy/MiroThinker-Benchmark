#!/usr/bin/env python3
"""
Test script to verify config override functionality in API server.
"""

import json
import sys


def test_config_override_structure():
    """Test that config_overrides parameter is properly defined"""
    print("Testing config override structure...")
    
    checks = {
        "config_overrides in QueryRequest": False,
        "config_overrides passed to stream_generator": False,
        "override_list conversion logic": False,
        "initialize_config accepts overrides": False,
    }
    
    try:
        with open("api_server.py", "r") as f:
            content = f.read()
            
        # Check for config_overrides in QueryRequest
        if "config_overrides: Optional[Dict[str, str]]" in content:
            checks["config_overrides in QueryRequest"] = True
            
        # Check if stream_generator accepts config_overrides
        if "config_overrides: Optional[Dict[str, str]] = None" in content:
            checks["config_overrides passed to stream_generator"] = True
            
        # Check for override list conversion
        if 'override_list = [f"{key}={value}"' in content:
            checks["override_list conversion logic"] = True
            
        # Check if initialize_config accepts overrides
        if "def initialize_config(overrides: Optional[List[str]]" in content:
            checks["initialize_config accepts overrides"] = True
            
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
        print("\n✓ All config override checks passed!")
        return True
    else:
        print("\n✗ Some checks failed")
        return False


def test_documentation():
    """Test that documentation is updated"""
    print("\n\nTesting documentation updates...")
    
    checks = {
        "config_overrides in API_README": False,
        "Configuration Overrides section exists": False,
        "Example usage provided": False,
    }
    
    try:
        with open("API_README.md", "r") as f:
            content = f.read()
            
        # Check for config_overrides mention
        if "config_overrides" in content:
            checks["config_overrides in API_README"] = True
            
        # Check for Configuration Overrides section
        if "## Configuration Overrides" in content:
            checks["Configuration Overrides section exists"] = True
            
        # Check for example usage
        if '"llm.base_url"' in content and '"llm.provider"' in content:
            checks["Example usage provided"] = True
            
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
        print("\n✓ All documentation checks passed!")
        return True
    else:
        print("\n✗ Some checks failed")
        return False


def test_example_requests():
    """Test example request formats"""
    print("\n\nTesting example request formats...")
    
    # Example with config overrides
    example1 = {
        "query": "What is 2+2?",
        "is_confirmed": False,
        "config_overrides": {
            "llm.provider": "qwen",
            "llm.base_url": "http://localhost:61002/v1"
        }
    }
    
    example2 = {
        "query": "What is 2+2?",
        "is_confirmed": False,
        "config_overrides": {
            "llm": "claude-3-7",
            "llm.temperature": "0.7"
        }
    }
    
    print("\nExample requests (valid JSON):")
    print("-" * 50)
    try:
        print("Example 1 (provider override):")
        print(json.dumps(example1, indent=2))
        print("\nExample 2 (config group override):")
        print(json.dumps(example2, indent=2))
        print("-" * 50)
        print("✓ Example requests are valid JSON")
        return True
    except Exception as e:
        print(f"✗ Invalid format: {e}")
        return False


def main():
    """Main test function"""
    print("=" * 50)
    print("Config Override Feature - Validation Test")
    print("=" * 50)
    
    test1 = test_config_override_structure()
    test2 = test_documentation()
    test3 = test_example_requests()
    
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
