#!/usr/bin/env python3
import os
import requests
from pathlib import Path

# Load env manually
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ[k.strip()] = v.strip().strip('"')

API_URL = os.getenv("AIR_API_URL", "https://air.nvidia.com")
resp = requests.post(f"{API_URL}/api/v1/login/", data={
    'username': os.getenv("AIR_USERNAME"),
    'password': os.getenv("AIR_API_TOKEN")
})
jwt = resp.json().get('token')
headers = {"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}

# Read lines from cloud-init
cloud_init_path = Path(__file__).parent.parent / "cloud-init-password.yaml"
lines = cloud_init_path.read_text().splitlines()

print(f"Testing {len(lines)} lines individually:\n")

for i, line in enumerate(lines):
    content = f"#cloud-config\n{line}"
    payload = {"name": f"line-test-{i}", "kind": "cloud-init-user-data", "organization": None, "content": content}
    resp = requests.post(f"{API_URL}/api/v2/userconfigs/", headers=headers, json=payload)
    if resp.status_code == 201:
        requests.delete(f"{API_URL}/api/v2/userconfigs/{resp.json()['id']}/", headers=headers)
        status = "✓"
    else:
        status = "✗"
    print(f"{status} Line {i+1:2}: {line[:70]}")

