#!/usr/bin/env python3
"""
Test if content size is causing the 403.
"""

import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import requests

API_URL = os.getenv("AIR_API_URL", "https://air.nvidia.com")
API_TOKEN = os.getenv("AIR_API_TOKEN")
USERNAME = os.getenv("AIR_USERNAME")

if not API_TOKEN or not USERNAME:
    print("ERROR: Set AIR_API_TOKEN and AIR_USERNAME")
    sys.exit(1)

# Login
resp = requests.post(f"{API_URL}/api/v1/login/", data={
    'username': USERNAME,
    'password': API_TOKEN
})
jwt = resp.json().get('token')
headers = {
    "Authorization": f"Bearer {jwt}",
    "Content-Type": "application/json"
}

print("Testing different content sizes:\n")

# Test various sizes
test_cases = [
    ("tiny", "#cloud-config\npassword: test"),  # ~30 bytes
    ("small", "#cloud-config\npassword: test123\n" + "# padding\n" * 50),  # ~500 bytes
    ("medium", "#cloud-config\npassword: test123\n" + "# padding line here\n" * 100),  # ~2000 bytes
    ("large", "#cloud-config\npassword: test123\n" + "# padding line for testing\n" * 200),  # ~5500 bytes
]

for name, content in test_cases:
    payload = {
        "name": f"size-test-{name}",
        "kind": "cloud-init-user-data",
        "organization": None,
        "content": content
    }
    
    resp = requests.post(f"{API_URL}/api/v2/userconfigs/", headers=headers, json=payload)
    status = "✓" if resp.status_code == 201 else "✗"
    
    print(f"{status} {name:10} ({len(content):5} bytes): {resp.status_code}")
    
    # Cleanup if successful
    if resp.status_code == 201:
        config_id = resp.json().get('id')
        requests.delete(f"{API_URL}/api/v2/userconfigs/{config_id}/", headers=headers)
    elif resp.status_code == 403:
        # Check if it's Akamai
        if 'Access Denied' in resp.text:
            print(f"       ^ Akamai WAF block detected")

print("\nIf larger sizes fail, the WAF is blocking based on payload size.")

