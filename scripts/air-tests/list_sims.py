#!/usr/bin/env python3
"""List all NVIDIA Air simulations for the authenticated user."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict

import requests


REPO_ROOT = Path(__file__).resolve().parents[2]


def parse_dotenv(path: Path) -> Dict[str, str]:
    """Minimal .env parser."""
    env: Dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
            v = v[1:-1]
        env[k] = v
    return env


def main() -> int:
    env_path = REPO_ROOT / ".env"
    env = parse_dotenv(env_path)
    
    username = env.get("AIR_USERNAME")
    api_token = env.get("AIR_API_TOKEN")
    api_url = env.get("AIR_API_URL", "https://air.nvidia.com")
    
    print(f"API: {api_url}")
    print(f"User: {username}\n")
    
    # Login
    login_url = f"{api_url.rstrip('/')}/api/v1/login/"
    resp = requests.post(login_url, data={"username": username, "password": api_token}, timeout=30)
    resp.raise_for_status()
    jwt = resp.json()["token"]
    
    # List simulations
    list_url = f"{api_url.rstrip('/')}/api/v2/simulations/"
    resp = requests.get(list_url, headers={"Authorization": f"Bearer {jwt}"}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    
    results = data.get("results", data) if isinstance(data, dict) else data
    
    print(f"Found {len(results)} simulation(s):\n")
    print(f"{'ID':<40} {'State':<12} {'Title'}")
    print("-" * 90)
    
    for sim in results:
        sim_id = sim.get("id", "?")
        state = sim.get("state", "?")
        title = sim.get("title", sim.get("name", "?"))
        error = sim.get("error") or sim.get("state_message") or ""
        print(f"{sim_id:<40} {state:<12} {title}")
        if error:
            print(f"{'':40} {'':12} └─ {error}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

