#!/usr/bin/env python3
"""
One-time setup script to create the UserConfig for cloud-init.

UserConfigs are user-level resources (not simulation-specific), so they can be
created once and reused across all simulations. Run this script once to create
the config, then deploy_bcm_air.py will find and reuse it automatically.

This is useful if:
- The UserConfig API is rate-limited during full deployment
- You want to pre-configure before running deployments

Usage:
    python scripts/setup_userconfig.py
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

# Configuration
API_URL = os.getenv("AIR_API_URL", "https://air.nvidia.com")
API_TOKEN = os.getenv("AIR_API_TOKEN")
USERNAME = os.getenv("AIR_USERNAME")
DEFAULT_PASSWORD = os.getenv("DEFAULT_PASSWORD", "Nvidia1234!")

USERCONFIG_NAME = "bcm-cloudinit-password"  # Must match deploy_bcm_air.py

if not API_TOKEN or not USERNAME:
    print("ERROR: Set AIR_API_TOKEN and AIR_USERNAME environment variables")
    sys.exit(1)

# Find cloud-init template
script_dir = Path(__file__).parent
project_root = script_dir.parent
cloudinit_path = project_root / "cloud-init-password.yaml"
template_path = project_root / "sample-configs" / "cloud-init-password.yaml.example"

if cloudinit_path.exists():
    content = cloudinit_path.read_text()
elif template_path.exists():
    content = template_path.read_text()
    # Substitute password placeholder
    content = content.replace('{PASSWORD}', DEFAULT_PASSWORD)
else:
    print("ERROR: No cloud-init config found!")
    print(f"  Expected: {cloudinit_path}")
    print(f"  Or template: {template_path}")
    sys.exit(1)

print(f"API URL: {API_URL}")
print(f"Username: {USERNAME}")
print(f"UserConfig name: {USERCONFIG_NAME}")
print(f"Content size: {len(content)} bytes")
print()

# Login
print("Authenticating...")
resp = requests.post(f"{API_URL}/api/v1/login/", data={
    'username': USERNAME,
    'password': API_TOKEN
})
if resp.status_code != 200:
    print(f"Login failed: {resp.text}")
    sys.exit(1)
jwt = resp.json().get('token')
print("✓ Authenticated")

headers = {
    "Authorization": f"Bearer {jwt}",
    "Content-Type": "application/json"
}

# Check if config already exists
print(f"\nChecking for existing UserConfig '{USERCONFIG_NAME}'...")
resp = requests.get(f"{API_URL}/api/v2/userconfigs/", headers=headers)
if resp.status_code != 200:
    print(f"Failed to list UserConfigs: {resp.status_code}")
    print(resp.text[:300])
    sys.exit(1)

existing_id = None
for cfg in resp.json().get('results', []):
    if cfg.get('name') == USERCONFIG_NAME:
        existing_id = cfg.get('id')
        break

if existing_id:
    print(f"✓ Found existing config: {existing_id}")
    
    # Update content
    print("Updating content...")
    resp = requests.patch(
        f"{API_URL}/api/v2/userconfigs/{existing_id}/",
        headers=headers,
        json={"content": content}
    )
    if resp.status_code == 200:
        print("✓ Content updated!")
    else:
        print(f"Failed to update: {resp.status_code}")
        print(resp.text[:300])
else:
    # Create new
    print("Creating new UserConfig...")
    payload = {
        "name": USERCONFIG_NAME,
        "kind": "cloud-init-user-data",
        "organization": None,
        "content": content
    }
    resp = requests.post(f"{API_URL}/api/v2/userconfigs/", headers=headers, json=payload)
    
    if resp.status_code == 201:
        config_id = resp.json().get('id')
        print(f"✓ Created UserConfig: {config_id}")
    else:
        print(f"Failed to create: {resp.status_code}")
        print(resp.text[:500])
        sys.exit(1)

print("\n" + "=" * 60)
print("Setup complete!")
print("The deploy_bcm_air.py script will now find and reuse this config.")
print("=" * 60)

