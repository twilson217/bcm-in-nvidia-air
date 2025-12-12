#!/usr/bin/env python3
"""
Test script to reproduce UserConfig API access issue on air.nvidia.com free tier.
The API returns "Access Denied" when trying to create or list UserConfigs.

Usage:
    export AIR_API_TOKEN="your_token_here"
    python scripts/test_userconfig_api.py
"""

import os
import requests

# Configuration
API_URL = os.getenv("AIR_API_URL", "https://air.nvidia.com")  # External Air (free tier)
API_TOKEN = os.getenv("AIR_API_TOKEN")

if not API_TOKEN:
    print("ERROR: AIR_API_TOKEN environment variable not set")
    print("Run: export AIR_API_TOKEN=your_token_here")
    exit(1)

headers = {
    "Authorization": f"Bearer {API_TOKEN}",
    "Content-Type": "application/json"
}

print(f"Testing against: {API_URL}")
print(f"Token: {API_TOKEN[:10]}...{API_TOKEN[-4:]}")
print()

# Test 1: Try to list UserConfigs
print("=" * 60)
print("Test 1: GET /api/v2/userconfigs/")
print("=" * 60)
response = requests.get(f"{API_URL}/api/v2/userconfigs/", headers=headers)
print(f"Status: {response.status_code}")
print(f"Response: {response.text[:500]}")

# Test 2: Try to create a UserConfig
print("\n" + "=" * 60)
print("Test 2: POST /api/v2/userconfigs/")
print("=" * 60)
payload = {
    "name": "test-cloud-init-config",
    "kind": "cloud-init-user-data",
    "content": "#cloud-config\npassword: TestPassword123\nchpasswd:\n  expire: false"
}
response = requests.post(f"{API_URL}/api/v2/userconfigs/", headers=headers, json=payload)
print(f"Status: {response.status_code}")
print(f"Response: {response.text[:500]}")

# Test 3: Verify other API endpoints work (simulations list)
print("\n" + "=" * 60)
print("Test 3: GET /api/v2/simulations/ (control - should work)")
print("=" * 60)
response = requests.get(f"{API_URL}/api/v2/simulations/", headers=headers)
print(f"Status: {response.status_code}")
if response.status_code == 200:
    data = response.json()
    print(f"Response: Found {data.get('count', 0)} simulations")
else:
    print(f"Response: {response.text[:200]}...")

print("\n" + "=" * 60)
print("Summary:")
print("- If Test 1 & 2 return 'Access Denied' but Test 3 works,")
print("  this confirms UserConfig API is restricted for this account.")
print("=" * 60)

