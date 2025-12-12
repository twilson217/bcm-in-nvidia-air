#!/usr/bin/env python3
"""
Test if the actual cloud-init content triggers WAF.
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

# Read actual cloud-init file
cloudinit_path = Path(__file__).parent.parent / "cloud-init-password.yaml"
actual_content = cloudinit_path.read_text()

print("Testing content patterns that might trigger WAF:\n")

test_cases = [
    ("plain_password", "#cloud-config\npassword: Nvidia1234!"),
    ("chpasswd_only", """#cloud-config
chpasswd:
  users:
    - name: root
      password: Nvidia1234!
      type: text
  expire: false
"""),
    ("with_ssh_key", """#cloud-config
ssh_authorized_keys:
  - ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQ...test
"""),
    ("with_runcmd", """#cloud-config
runcmd:
  - echo "hello"
  - mkdir -p /root/.ssh
"""),
    ("with_sed", """#cloud-config
runcmd:
  - sed -i 's/test/test2/' /etc/test
"""),
    ("with_sshd_config", """#cloud-config
runcmd:
  - sed -i 's/^#*PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
"""),
    ("with_systemctl", """#cloud-config
runcmd:
  - systemctl restart ssh
"""),
    ("full_actual", actual_content),
]

for name, content in test_cases:
    payload = {
        "name": f"content-test-{name[:20]}",
        "kind": "cloud-init-user-data",
        "organization": None,
        "content": content
    }
    
    resp = requests.post(f"{API_URL}/api/v2/userconfigs/", headers=headers, json=payload)
    status = "✓" if resp.status_code == 201 else "✗"
    
    print(f"{status} {name:20} ({len(content):5} bytes): {resp.status_code}")
    
    if resp.status_code == 201:
        config_id = resp.json().get('id')
        requests.delete(f"{API_URL}/api/v2/userconfigs/{config_id}/", headers=headers)
    elif resp.status_code == 403:
        if 'Access Denied' in resp.text:
            print(f"       ^ WAF blocked this content pattern!")

print("\nThis should identify which content pattern triggers the WAF.")

