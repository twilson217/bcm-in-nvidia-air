#!/usr/bin/env python3
"""
Minimal reproduction script for UserConfig API access issue on air.nvidia.com.

Issue: Free tier accounts get 403 "Access Denied" when trying to create UserConfigs.
The error comes from Akamai CDN, not the API itself.

Prerequisites:
    pip install python-dotenv requests

Usage:
    export AIR_API_TOKEN="your_token_here"
    export AIR_USERNAME="your_email@example.com"
    python test_userconfig_api.py
    
Or create a .env file with those variables.

Expected behavior for free tier:
- Login succeeds (200)
- GET /api/v2/userconfigs/ may succeed (200) or fail (403)
- POST /api/v2/userconfigs/ fails with 403 "Access Denied" from Akamai CDN
"""

import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv is optional

import requests

API_URL = os.getenv("AIR_API_URL", "https://air.nvidia.com")
API_TOKEN = os.getenv("AIR_API_TOKEN")
USERNAME = os.getenv("AIR_USERNAME")

if not API_TOKEN or not USERNAME:
    print("ERROR: Set AIR_API_TOKEN and AIR_USERNAME environment variables")
    print("  export AIR_API_TOKEN=your_token")
    print("  export AIR_USERNAME=your_email@example.com")
    sys.exit(1)

print(f"API URL: {API_URL}")
print(f"Username: {USERNAME}")
print(f"Token: {API_TOKEN[:8]}...{API_TOKEN[-4:]}")
print()

# Step 1: Login to get JWT token
print("=" * 60)
print("Step 1: POST /api/v1/login/")
print("=" * 60)
resp = requests.post(f"{API_URL}/api/v1/login/", data={
    'username': USERNAME,
    'password': API_TOKEN
})
print(f"Status: {resp.status_code}")
if resp.status_code != 200:
    print(f"Response: {resp.text[:500]}")
    print("\nLogin failed!")
    sys.exit(1)

jwt = resp.json().get('token')
print(f"✓ JWT token obtained")

headers = {
    "Authorization": f"Bearer {jwt}",
    "Content-Type": "application/json"
}

# Step 2: List UserConfigs (GET)
print("\n" + "=" * 60)
print("Step 2: GET /api/v2/userconfigs/")
print("=" * 60)
resp = requests.get(f"{API_URL}/api/v2/userconfigs/", headers=headers)
print(f"Status: {resp.status_code}")
if resp.status_code == 200:
    print(f"✓ Success: {resp.json().get('count', 0)} configs found")
else:
    print(f"Response: {resp.text[:300]}")

# Step 3: Create UserConfig (POST) - this is what fails
print("\n" + "=" * 60)
print("Step 3: POST /api/v2/userconfigs/")
print("=" * 60)
payload = {
    "name": "test-config-reproduction",
    "kind": "cloud-init-user-data",
    "organization": None,  # Explicitly null, not omitted
    "content": "#cloud-config\npassword: test123"
}
print(f"Payload: {payload}")
resp = requests.post(f"{API_URL}/api/v2/userconfigs/", headers=headers, json=payload)
print(f"Status: {resp.status_code}")
print(f"Response: {resp.text[:500]}")

if resp.status_code == 201:
    # Clean up
    config_id = resp.json().get('id')
    print(f"\n✓ Created successfully! Cleaning up...")
    requests.delete(f"{API_URL}/api/v2/userconfigs/{config_id}/", headers=headers)

# Step 4: Control test - simulations endpoint
print("\n" + "=" * 60)
print("Step 4: GET /api/v2/simulations/ (control)")
print("=" * 60)
resp = requests.get(f"{API_URL}/api/v2/simulations/", headers=headers)
print(f"Status: {resp.status_code}")
if resp.status_code == 200:
    print(f"✓ Success: {resp.json().get('count', 0)} simulations found")
else:
    print(f"Response: {resp.text[:300]}")

print("\n" + "=" * 60)
print("Summary")
print("=" * 60)
print("""
If Step 3 returns 403 with "Access Denied" from Akamai CDN,
this confirms the UserConfig API is blocked for free tier accounts.

The error looks like:
  <HTML><HEAD><TITLE>Access Denied</TITLE></HEAD>
  <BODY>...Reference #18.xxxxx...</BODY></HTML>

This is a CDN/WAF block, not an API permission error.
""")
