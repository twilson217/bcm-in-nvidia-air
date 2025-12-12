#!/usr/bin/env python3
"""
Clean up duplicate UserConfigs from your NVIDIA Air account.

Lists all UserConfigs and optionally deletes duplicates/old test configs.

Usage:
    ./scripts/cleanup_userconfigs.py           # List all configs
    ./scripts/cleanup_userconfigs.py --delete  # Delete duplicates (keeps 'bcm-cloudinit-password')
"""

import os
import sys
import argparse
from pathlib import Path
from collections import defaultdict

# Load env manually
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ[k.strip()] = v.strip().strip('"')

import requests

API_URL = os.getenv("AIR_API_URL", "https://air.nvidia.com")
API_TOKEN = os.getenv("AIR_API_TOKEN")
USERNAME = os.getenv("AIR_USERNAME")

if not API_TOKEN or not USERNAME:
    print("ERROR: Set AIR_API_TOKEN and AIR_USERNAME in .env")
    sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Clean up UserConfigs")
    parser.add_argument("--delete", action="store_true", help="Delete duplicates and test configs")
    parser.add_argument("--keep", default="bcm-cloudinit-password", help="Name of config to keep (default: bcm-cloudinit-password)")
    args = parser.parse_args()
    
    # Login
    print(f"Authenticating to {API_URL}...")
    resp = requests.post(f"{API_URL}/api/v1/login/", data={
        'username': USERNAME,
        'password': API_TOKEN
    })
    if resp.status_code != 200:
        print(f"Login failed: {resp.status_code}")
        sys.exit(1)
    jwt = resp.json().get('token')
    headers = {"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}
    print("✓ Authenticated\n")
    
    # Get all UserConfigs
    print("Fetching UserConfigs...")
    all_configs = []
    url = f"{API_URL}/api/v2/userconfigs/"
    
    while url:
        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            print(f"Failed to list configs: {resp.status_code}")
            sys.exit(1)
        data = resp.json()
        all_configs.extend(data.get('results', []))
        url = data.get('next')
    
    print(f"Found {len(all_configs)} UserConfigs\n")
    
    if not all_configs:
        print("No configs to clean up!")
        return
    
    # Group by name
    by_name = defaultdict(list)
    for cfg in all_configs:
        by_name[cfg['name']].append(cfg)
    
    # Categorize
    keep_config = None
    duplicates = []
    test_configs = []
    
    for name, configs in by_name.items():
        if name == args.keep:
            # Keep the first one, mark others as duplicates
            keep_config = configs[0]
            duplicates.extend(configs[1:])
        elif name.startswith(('test-', 'line-test-', 'waf-test-', 'size-test-', 'content-test-', 'bcm-password-config-')):
            test_configs.extend(configs)
        elif len(configs) > 1:
            # Keep first, duplicates are the rest
            duplicates.extend(configs[1:])
    
    # Display
    print("=" * 60)
    print("UserConfigs Summary")
    print("=" * 60)
    
    if keep_config:
        print(f"\n✓ Keep: {args.keep}")
        print(f"  ID: {keep_config['id']}")
    
    print(f"\nDuplicates: {len(duplicates)}")
    for cfg in duplicates[:10]:
        print(f"  - {cfg['name']} ({cfg['id'][:8]}...)")
    if len(duplicates) > 10:
        print(f"  ... and {len(duplicates) - 10} more")
    
    print(f"\nTest configs: {len(test_configs)}")
    for cfg in test_configs[:10]:
        print(f"  - {cfg['name']} ({cfg['id'][:8]}...)")
    if len(test_configs) > 10:
        print(f"  ... and {len(test_configs) - 10} more")
    
    other_configs = [c for c in all_configs if c not in duplicates and c not in test_configs and c != keep_config]
    if other_configs:
        print(f"\nOther configs: {len(other_configs)}")
        for cfg in other_configs:
            print(f"  - {cfg['name']} ({cfg['id'][:8]}...)")
    
    to_delete = duplicates + test_configs
    
    if not args.delete:
        print(f"\n" + "=" * 60)
        print(f"Total to delete: {len(to_delete)}")
        print(f"Run with --delete to remove them")
        print("=" * 60)
        return
    
    # Delete
    if not to_delete:
        print("\nNothing to delete!")
        return
    
    print(f"\n" + "=" * 60)
    print(f"Deleting {len(to_delete)} configs...")
    print("=" * 60)
    
    deleted = 0
    failed = 0
    for cfg in to_delete:
        resp = requests.delete(f"{API_URL}/api/v2/userconfigs/{cfg['id']}/", headers=headers)
        if resp.status_code in (200, 204):
            deleted += 1
            print(f"  ✓ Deleted: {cfg['name']}")
        else:
            failed += 1
            print(f"  ✗ Failed: {cfg['name']} ({resp.status_code})")
    
    print(f"\nDone! Deleted: {deleted}, Failed: {failed}")

if __name__ == "__main__":
    main()

