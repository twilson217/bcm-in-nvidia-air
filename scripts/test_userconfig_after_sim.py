#!/usr/bin/env python3
"""
Test UserConfig creation immediately after simulation creation.
This mimics what deploy_bcm_air.py does to see if the 403 is timing-related.
"""

import os
import sys
import time
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

print(f"API URL: {API_URL}")
print(f"Username: {USERNAME}")
print()

# Step 1: Login
print("=" * 60)
print("Step 1: Login")
print("=" * 60)
resp = requests.post(f"{API_URL}/api/v1/login/", data={
    'username': USERNAME,
    'password': API_TOKEN
})
if resp.status_code != 200:
    print(f"Login failed: {resp.text}")
    sys.exit(1)
jwt = resp.json().get('token')
print(f"✓ JWT obtained")

headers = {
    "Authorization": f"Bearer {jwt}",
    "Content-Type": "application/json"
}

# Step 2: Create a minimal simulation (like deploy script does)
print("\n" + "=" * 60)
print("Step 2: Create simulation (minimal)")
print("=" * 60)

# Minimal topology - just one node
topology = {
    "nodes": {
        "test-node": {
            "os": "generic/ubuntu2204"
        }
    },
    "links": []
}

sim_payload = {
    "title": "test-userconfig-timing",
    "topology": topology,
    "oob": False
}

resp = requests.post(
    f"{API_URL}/api/v2/simulations/",
    headers=headers,
    json=sim_payload
)
print(f"Status: {resp.status_code}")

sim_id = None
if resp.status_code == 201:
    sim_id = resp.json().get('id')
    print(f"✓ Simulation created: {sim_id}")
else:
    print(f"Failed to create simulation: {resp.text[:300]}")
    print("\nTrying UserConfig anyway...")

# Step 3: Immediately try to create UserConfig (like deploy script)
print("\n" + "=" * 60)
print("Step 3: POST /api/v2/userconfigs/ (immediately after sim)")
print("=" * 60)

# Use the actual cloud-init content from our file
cloudinit_path = Path(__file__).parent.parent / "cloud-init-password.yaml"
if cloudinit_path.exists():
    content = cloudinit_path.read_text()
    print(f"Using real cloud-init content ({len(content)} bytes)")
else:
    content = "#cloud-config\npassword: test123"
    print("Using minimal test content")

payload = {
    "name": f"test-timing-{sim_id[:8] if sim_id else 'nosim'}",
    "kind": "cloud-init-user-data",
    "organization": None,
    "content": content
}

resp = requests.post(f"{API_URL}/api/v2/userconfigs/", headers=headers, json=payload)
print(f"Status: {resp.status_code}")

if resp.status_code == 201:
    config_id = resp.json().get('id')
    print(f"✓ UserConfig created: {config_id}")
    # Cleanup
    requests.delete(f"{API_URL}/api/v2/userconfigs/{config_id}/", headers=headers)
    print("  (cleaned up)")
elif resp.status_code == 403:
    print(f"✗ 403 - Access Denied!")
    print(f"Response: {resp.text[:500]}")
else:
    print(f"Response: {resp.text[:300]}")

# Step 4: Wait 5 seconds and try again
print("\n" + "=" * 60)
print("Step 4: Wait 5s, then try UserConfig again")
print("=" * 60)
print("Waiting 5 seconds...")
time.sleep(5)

payload["name"] = f"test-timing-delayed-{sim_id[:8] if sim_id else 'nosim'}"
resp = requests.post(f"{API_URL}/api/v2/userconfigs/", headers=headers, json=payload)
print(f"Status: {resp.status_code}")

if resp.status_code == 201:
    config_id = resp.json().get('id')
    print(f"✓ UserConfig created (after delay): {config_id}")
    requests.delete(f"{API_URL}/api/v2/userconfigs/{config_id}/", headers=headers)
    print("  (cleaned up)")
else:
    print(f"Response: {resp.text[:300]}")

# Cleanup simulation
if sim_id:
    print("\n" + "=" * 60)
    print("Cleanup: Delete simulation")
    print("=" * 60)
    resp = requests.delete(f"{API_URL}/api/v2/simulations/{sim_id}/", headers=headers)
    print(f"Status: {resp.status_code}")

print("\n" + "=" * 60)
print("Summary")
print("=" * 60)
print("""
If Step 3 fails with 403 but Step 4 succeeds, it's a rate limit issue.
If both fail, it might be content-related (size, characters).
If both succeed, the issue is elsewhere in the deploy script.
""")

