#!/usr/bin/env python3
"""
Test script for multimodal input processing via API endpoints.

This script tests the multimodal input feature by:
1. Uploading an image file using /v1/api/upload
2. Executing a query to have the agent read and describe the image using /v1/api/execute

Usage:
    python test_multimodal_api.py

Requirements:
    - The API server must be running on localhost:8000
    - Set IMAGE_FILE_PATH to the path of an image file to test with
"""

import json
import os
import sys
import requests
import uuid

# ============================================================
# Configuration - MODIFY THESE VALUES AS NEEDED
# ============================================================

# API server base URL
API_BASE_URL = "http://localhost:8000"

# Path to the image file to test with (PLACEHOLDER - modify this path)
IMAGE_FILE_PATH = "/path/to/your/test/image.png"  # TODO: Replace with actual image path

# Session ID for the test
SESSION_ID = f"test_multimodal_{uuid.uuid4().hex[:8]}"

# Optional: Bearer token for authentication (set to None if not required)
AUTH_TOKEN = None  # e.g., "your-bearer-token"

# ============================================================
# API Helper Functions
# ============================================================


def get_headers(content_type: str = None) -> dict:
    """Build request headers with session ID and optional auth."""
    headers = {
        "X-Session-Id": SESSION_ID,
    }
    if content_type:
        headers["Content-Type"] = content_type
    if AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {AUTH_TOKEN}"
    return headers


def upload_file(file_path: str) -> dict:
    """
    Upload a file to the sandbox using /v1/api/upload endpoint.
    
    Args:
        file_path: Local path to the file to upload
        
    Returns:
        API response as dict containing the sandbox file path
    """
    url = f"{API_BASE_URL}/v1/api/upload"
    
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
    
    filename = os.path.basename(file_path)
    
    with open(file_path, "rb") as f:
        files = {"file": (filename, f)}
        headers = get_headers()  # Don't set Content-Type for multipart
        response = requests.post(url, headers=headers, files=files)
    
    if response.status_code != 200:
        raise Exception(f"Upload failed with status {response.status_code}: {response.text}")
    
    return response.json()


def execute_query(query: str, config_overrides: dict = None) -> str:
    """
    Execute a query using /v1/api/execute endpoint and collect streaming response.
    
    Args:
        query: The query to execute
        config_overrides: Optional Hydra config overrides
        
    Returns:
        Complete response text collected from the stream
    """
    url = f"{API_BASE_URL}/v1/api/execute"
    
    payload = {
        "message": [
            {
                "type": "query",
                "step": 1,
                "content": query
            }
        ]
    }
    
    if config_overrides:
        payload["config_overrides"] = config_overrides
    
    headers = get_headers(content_type="application/json")
    
    response = requests.post(url, headers=headers, json=payload, stream=True)
    
    if response.status_code != 200:
        raise Exception(f"Execute failed with status {response.status_code}: {response.text}")
    
    # Collect the streaming response
    full_response = []
    for line in response.iter_lines():
        if line:
            try:
                event = json.loads(line.decode("utf-8"))
                event_type = event.get("type", "")
                delta = event.get("delta", "")
                
                # Print events as they arrive
                if event_type == "start":
                    print(f"[START] Step {event.get('step', '')}")
                elif event_type == "answer":
                    print(delta, end="", flush=True)
                    full_response.append(delta)
                elif event_type == "end":
                    print(f"\n[END] Step {event.get('step', '')}")
                else:
                    print(f"[{event_type}] {delta}")
                    
            except json.JSONDecodeError as e:
                print(f"[DECODE ERROR] {line}: {e}")
    
    return "".join(full_response)


# ============================================================
# Test Functions
# ============================================================


def test_upload_and_describe_image():
    """
    Test uploading an image and having the agent describe its content.
    
    This test:
    1. Uploads an image file to the sandbox
    2. Asks the agent to read and describe the image content
    """
    print("=" * 60)
    print("Multimodal API Test: Upload and Describe Image")
    print("=" * 60)
    
    # Check if image file exists
    if not os.path.exists(IMAGE_FILE_PATH):
        print(f"\n❌ ERROR: Image file not found: {IMAGE_FILE_PATH}")
        print("Please set IMAGE_FILE_PATH to a valid image file path.")
        return False
    
    print(f"\n📁 Image file: {IMAGE_FILE_PATH}")
    print(f"🔑 Session ID: {SESSION_ID}")
    print(f"🌐 API Base URL: {API_BASE_URL}")
    
    # Step 1: Upload the image
    print("\n" + "-" * 40)
    print("Step 1: Uploading image to sandbox...")
    print("-" * 40)
    
    try:
        upload_result = upload_file(IMAGE_FILE_PATH)
        print(f"✅ Upload successful!")
        print(f"   Response: {json.dumps(upload_result, indent=2)}")
        
        sandbox_path = upload_result.get("data", {}).get("path", "")
        if not sandbox_path:
            print("❌ ERROR: Could not get sandbox file path from upload response")
            return False
            
        print(f"   Sandbox path: {sandbox_path}")
        
    except Exception as e:
        print(f"❌ Upload failed: {e}")
        return False
    
    # Step 2: Execute query to describe the image
    print("\n" + "-" * 40)
    print("Step 2: Asking agent to describe the image...")
    print("-" * 40)
    
    # Build query that references the uploaded file
    query = f"Please look at the image file at {sandbox_path} and describe what you see in detail. What is the content of this image?"
    
    print(f"Query: {query}\n")
    
    # Optional: Use single_agent_keep5 agent which has multimodal_input enabled
    config_overrides = {
        "agent": "single_agent_keep5"
    }
    
    try:
        print("Agent response:")
        print("-" * 40)
        response = execute_query(query, config_overrides=config_overrides)
        print("-" * 40)
        
        if response:
            print(f"\n✅ Agent provided a response ({len(response)} characters)")
            return True
        else:
            print("\n⚠️ Agent returned empty response")
            return False
            
    except Exception as e:
        print(f"\n❌ Execute failed: {e}")
        return False


def main():
    """Main entry point."""
    print("\n" + "=" * 60)
    print("MiroFlow Agent - Multimodal API Integration Test")
    print("=" * 60)
    
    # Check configuration
    print("\nConfiguration:")
    print(f"  - API URL: {API_BASE_URL}")
    print(f"  - Image Path: {IMAGE_FILE_PATH}")
    print(f"  - Session ID: {SESSION_ID}")
    
    if IMAGE_FILE_PATH == "/path/to/your/test/image.png":
        print("\n⚠️  WARNING: IMAGE_FILE_PATH is still set to placeholder value!")
        print("    Please edit this script and set IMAGE_FILE_PATH to a real image file.")
        print("\n    Example:")
        print('    IMAGE_FILE_PATH = "/home/user/test_images/sample.png"')
        return 1
    
    # Run the test
    success = test_upload_and_describe_image()
    
    # Summary
    print("\n" + "=" * 60)
    if success:
        print("✅ TEST PASSED")
    else:
        print("❌ TEST FAILED")
    print("=" * 60)
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
