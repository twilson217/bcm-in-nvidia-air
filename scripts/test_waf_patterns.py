#!/usr/bin/env python3
"""
Test specific patterns that might trigger WAF.
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

print("Testing specific WAF trigger patterns:\n")

test_cases = [
    ("word_sed", "#cloud-config\n# sed"),
    ("path_etc_ssh", "#cloud-config\n# /etc/ssh/"),
    ("path_sshd_config", "#cloud-config\n# /etc/ssh/sshd_config"),
    ("path_sshd_config_d", "#cloud-config\n# /etc/ssh/sshd_config.d/"),
    ("permitrootlogin", "#cloud-config\n# PermitRootLogin yes"),
    ("write_files_ssh", """#cloud-config
write_files:
  - path: /etc/ssh/test.conf
    content: test
"""),
    ("write_files_other", """#cloud-config
write_files:
  - path: /tmp/test.conf
    content: test
"""),
    ("disable_root", "#cloud-config\ndisable_root: false"),
    ("echo_rsa_key", """#cloud-config
runcmd:
  - echo "ssh-rsa AAAA..." >> /root/.ssh/authorized_keys
"""),
    ("long_ssh_key", """#cloud-config
ssh_authorized_keys:
  - ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQCspEJ/kXj10dFrcZzfLrbd63qAzrlbtaX3Ow0/L5/2dy3RNPms5kqQQXS+1ATrgreAkyXLpbh8AFgIH1+zC8pLhOr7JOnwGEeNJ3w8P/ZPSD6EnDsCXiDB1NsMYkIpHocLXxjrHNyUu6ZsfKhMR3Y4y5xP46DSfndAW5VX9FDsp1bq5Kdu18/FtAwckmMs39UAu/R8pHGqhLCHTI0Y8SXfFyBn/gaYL72wjGWyOuK5zddPGFJJAkGpHM+4DDlfuxMnMHiu0foolVIFdu/iWeXr18+vBzbhWmuD64KYGVA0j/JyHlOin2f3qX5pHH1zpgcXHJC1tsmRHRTH3UvOpYjxiulAxFX/BUcyaH+avN2qeo1EuspDUGMJDHypA0bCFA/dbPTfu5GueuSY7ztFmJB3VFEjYMuKS7doKE+ufFTJ6/0YUq6lC5OK1lhwQz8Wlv86BNy2lM8N2TKoleqCN581GhRNbzZfoVJzZjVguWci+59x8wjL87fV6TY96E+5qhYmvNPWBQW+2faORlbc8uvzqMh+rFCyZwgM4Pg/HSviclAw02VRAz4/gfBma3/EPJeFlEJW1Y3eXlPR22okOLNpSXInFvtsxxlEXg5nZgKstF+KilXpXkWgLXQIR1LgmCRg63/ml+vQnLmSD9HXgGCTqordCJL0KmbYmR4Md1TFEw== test
"""),
]

for name, content in test_cases:
    payload = {
        "name": f"waf-test-{name[:15]}",
        "kind": "cloud-init-user-data",
        "organization": None,
        "content": content
    }
    
    resp = requests.post(f"{API_URL}/api/v2/userconfigs/", headers=headers, json=payload)
    status = "✓" if resp.status_code == 201 else "✗"
    
    print(f"{status} {name:25}: {resp.status_code}")
    
    if resp.status_code == 201:
        config_id = resp.json().get('id')
        requests.delete(f"{API_URL}/api/v2/userconfigs/{config_id}/", headers=headers)

print("\n✗ = WAF blocked, ✓ = allowed")

