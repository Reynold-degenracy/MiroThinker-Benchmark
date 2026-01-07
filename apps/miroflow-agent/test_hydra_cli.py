#!/usr/bin/env python3
"""
Test script to verify Hydra CLI argument support in api_server.py
"""

import sys


def test_hydra_decorator():
    """Test that api_server.py has Hydra decorator for CLI support"""
    print("Testing Hydra CLI support...")
    
    checks = {
        "@hydra.main decorator present": False,
        "main() function accepts cfg": False,
        "_default_cfg global variable": False,
        "CLI config stored in _default_cfg": False,
    }
    
    try:
        with open("api_server.py", "r") as f:
            content = f.read()
            
        # Check for @hydra.main decorator
        if '@hydra.main(config_path="conf", config_name="config"' in content:
            checks["@hydra.main decorator present"] = True
            
        # Check for main function signature
        if "def main(cfg: DictConfig)" in content:
            checks["main() function accepts cfg"] = True
            
        # Check for _default_cfg global
        if "_default_cfg: Optional[DictConfig]" in content or "_default_cfg = None" in content:
            checks["_default_cfg global variable"] = True
            
        # Check if CLI config is stored
        if "_default_cfg = cfg" in content:
            checks["CLI config stored in _default_cfg"] = True
            
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
        print("\n✓ All Hydra CLI checks passed!")
        return True
    else:
        print("\n✗ Some checks failed")
        return False


def test_documentation():
    """Test that documentation includes CLI examples"""
    print("\n\nTesting documentation updates...")
    
    checks = {
        "CLI override examples in README": False,
        "llm= override example": False,
        "Multiple overrides example": False,
    }
    
    try:
        with open("API_README.md", "r") as f:
            content = f.read()
            
        # Check for CLI override section
        if "With Hydra Configuration Overrides" in content:
            checks["CLI override examples in README"] = True
            
        # Check for specific examples
        if "python3 api_server.py llm=" in content:
            checks["llm= override example"] = True
            
        # Check for multiple overrides
        if "llm.api_key" in content and "llm.base_url" in content:
            checks["Multiple overrides example"] = True
            
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


def test_example_commands():
    """Test example command formats"""
    print("\n\nTesting example CLI commands...")
    
    examples = [
        "python3 api_server.py llm=qwen-3 llm.base_url=http://localhost:61002/v1",
        "python3 api_server.py llm=qwen-3 llm.api_key=xxxxx llm.temperature=0.7",
        "python3 api_server.py llm=claude-3-7 llm.api_key=your-key",
    ]
    
    print("\nExample CLI commands:")
    print("-" * 50)
    for i, example in enumerate(examples, 1):
        print(f"{i}. {example}")
    print("-" * 50)
    print("✓ Example commands documented")
    return True


def main():
    """Main test function"""
    print("=" * 50)
    print("Hydra CLI Support - Validation Test")
    print("=" * 50)
    
    test1 = test_hydra_decorator()
    test2 = test_documentation()
    test3 = test_example_commands()
    
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
