#!/usr/bin/env python3
"""
Test script to reproduce UserConfig API access issue on air.nvidia.com free tier.
The API returns "Access Denied" when trying to create or list UserConfigs.

Usage:
    # Set environment variables (or use .env file)
    export AIR_API_TOKEN="your_token_here"
    export AIR_USERNAME="your_email@example.com"
    python scripts/test_userconfig_api.py
"""

import os
import sys
from dotenv import load_dotenv
import requests

# Load .env file if present
load_dotenv()

# Configuration
API_URL = os.getenv("AIR_API_URL", "https://air.nvidia.com")
API_TOKEN = os.getenv("AIR_API_TOKEN")
USERNAME = os.getenv("AIR_USERNAME")

if not API_TOKEN:
    print("ERROR: AIR_API_TOKEN environment variable not set")
    print("Run: export AIR_API_TOKEN=your_token_here")
    sys.exit(1)

if not USERNAME:
    print("ERROR: AIR_USERNAME environment variable not set")
    print("Run: export AIR_USERNAME=your_email@example.com")
    sys.exit(1)

print(f"Testing against: {API_URL}")
print(f"Username: {USERNAME}")
print(f"Token: {API_TOKEN[:10]}...{API_TOKEN[-4:]}")
print()

# Step 1: Authenticate to get JWT token
print("=" * 60)
print("Step 1: Authenticate (POST /api/v1/login/)")
print("=" * 60)
login_response = requests.post(
    f"{API_URL}/api/v1/login/",
    data={
        'username': USERNAME,
        'password': API_TOKEN
    },
    timeout=30
)
print(f"Status: {login_response.status_code}")

if login_response.status_code != 200:
    print(f"Response: {login_response.text[:500]}")
    print("\nAuthentication failed! Cannot continue tests.")
    sys.exit(1)

jwt_token = login_response.json().get('token')
if not jwt_token:
    print(f"Response: {login_response.text[:500]}")
    print("\nNo JWT token in response! Cannot continue tests.")
    sys.exit(1)

print(f"✓ Got JWT token: {jwt_token[:20]}...")

# Headers with JWT token for subsequent requests
headers = {
    "Authorization": f"Bearer {jwt_token}",
    "Content-Type": "application/json"
}

# Test 1: Try to list UserConfigs
print("\n" + "=" * 60)
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
    print(f"✓ Response: Found {data.get('count', 0)} simulations")
else:
    print(f"Response: {response.text[:200]}...")

print("\n" + "=" * 60)
print("Summary:")
print("- If Test 1 & 2 return 'Access Denied' but Test 3 works,")
print("  this confirms UserConfig API is restricted for this account.")
print("=" * 60)
