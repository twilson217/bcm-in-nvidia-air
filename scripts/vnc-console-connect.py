#!/usr/bin/env python3
"""
Get VNC console connection information for NVIDIA Air simulation nodes.

This script outputs SSH tunnel commands and VNC passwords for connecting
to node consoles via VNC.

Usage:
  python scripts/vnc-console-connect.py --sim-id <uuid>
  python scripts/vnc-console-connect.py --sim-name <name>
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]


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


def air_login(api_url: str, username: str, api_token: str) -> str:
    """Login to Air API and return JWT token."""
    login_url = f"{api_url.rstrip('/')}/api/v1/login/"
    resp = requests.post(
        login_url,
        data={"username": username, "password": api_token},
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json().get("token")
    if not token:
        raise RuntimeError(f"No token in response: {resp.json()}")
    return token


def find_simulation_by_name(api_url: str, jwt: str, name: str) -> Optional[str]:
    """Find simulation ID by name."""
    list_url = f"{api_url.rstrip('/')}/api/v2/simulations/"
    resp = requests.get(
        list_url,
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    
    results = data.get("results", data) if isinstance(data, dict) else data
    
    for sim in results:
        if sim.get("title") == name or sim.get("name") == name:
            return sim.get("id")
    
    return None


def get_simulation_nodes(api_url: str, jwt: str, sim_id: str) -> List[dict]:
    """Get simulation nodes."""
    url = f"{api_url.rstrip('/')}/api/v2/simulations/nodes/?simulation={sim_id}"
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", data) if isinstance(data, dict) else data


def parse_console_url(console_url: str) -> tuple[str, str, int]:
    """
    Parse console_url to extract worker hostname, internal IP, and port.
    
    Format: wss://worker38.air-inside.nvidia.com/192.168.82.138:18089
    Returns: (worker_hostname, internal_ip, port)
    """
    # Parse the URL
    parsed = urlparse(console_url)
    worker_hostname = parsed.netloc  # e.g., worker38.air-inside.nvidia.com
    
    # Path contains /IP:port
    path = parsed.path.lstrip("/")
    if ":" in path:
        internal_ip, port_str = path.rsplit(":", 1)
        port = int(port_str)
    else:
        internal_ip = path
        port = 0
    
    return worker_hostname, internal_ip, port


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Get VNC console connection information for NVIDIA Air simulation nodes.",
    )
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sim-id", help="Simulation UUID")
    group.add_argument("--sim-name", help="Simulation name")
    
    parser.add_argument(
        "--env",
        default=str(REPO_ROOT / ".env"),
        help="Path to env file (default: .env)",
    )
    parser.add_argument(
        "--local-port-start",
        type=int,
        default=5900,
        help="Starting local port for VNC tunnels (default: 5900)",
    )
    
    args = parser.parse_args()
    
    # Load env file
    env_path = Path(args.env)
    if not env_path.exists():
        # Try alternate locations
        for alt in [REPO_ROOT / ".env.internal", REPO_ROOT / ".env.external"]:
            if alt.exists():
                env_path = alt
                break
        else:
            print(f"✗ No env file found", file=sys.stderr)
            return 1
    
    env = parse_dotenv(env_path)
    
    # Get credentials
    username = env.get("AIR_USERNAME")
    api_token = env.get("AIR_API_TOKEN")
    
    if not username or not api_token:
        print("✗ Missing AIR_USERNAME or AIR_API_TOKEN in env file", file=sys.stderr)
        return 1
    
    api_url = env.get("AIR_API_URL", "https://air.nvidia.com")
    
    try:
        # Authenticate
        jwt = air_login(api_url, username, api_token)
        
        # Resolve simulation ID
        if args.sim_name:
            sim_id = find_simulation_by_name(api_url, jwt, args.sim_name)
            if not sim_id:
                print(f"✗ Simulation not found: {args.sim_name}", file=sys.stderr)
                return 1
        else:
            sim_id = args.sim_id
        
        # Get nodes
        nodes = get_simulation_nodes(api_url, jwt, sim_id)
        
        if not nodes:
            print("No nodes found in simulation", file=sys.stderr)
            return 1
        
        # Sort by name
        nodes.sort(key=lambda n: n.get("name", ""))
        
        # Print header
        print("=" * 70)
        print("VNC CONSOLE CONNECTION INFO")
        print("=" * 70)
        print()
        
        local_port = args.local_port_start
        
        for node in nodes:
            name = node.get("name", "unknown")
            console_url = node.get("console_url", "")
            console_port = node.get("console_port")
            serial_port = node.get("serial_port")
            console_password = node.get("console_password", "")
            state = node.get("state", "unknown")
            
            if not console_url:
                print(f"Host: {name}")
                print(f"  State: {state}")
                print(f"  (No console URL available)")
                print()
                continue
            
            # Parse the console URL to get connection details
            worker_hostname, internal_ip, _ = parse_console_url(console_url)
            
            print(f"Host: {name}")
            print(f"  State: {state}")
            print(f"  SSH Tunnel: ssh -L {local_port}:{internal_ip}:{console_port} {worker_hostname} -p {serial_port}")
            print(f"  VNC Connect: localhost:{local_port}")
            print(f"  VNC Password: {console_password}")
            print()
            
            local_port += 1
        
        # Print usage notes
        print("=" * 70)
        print("USAGE NOTES")
        print("=" * 70)
        print()
        print("1. Open an SSH tunnel to the node:")
        print("   ssh -L <local_port>:<internal_ip>:<console_port> <worker_host> -p <serial_port>")
        print()
        print("2. Connect VNC client to localhost:<local_port>")
        print()
        print("3. Enter the VNC password when prompted")
        print()
        
        return 0
        
    except requests.exceptions.HTTPError as e:
        print(f"✗ HTTP error: {e}", file=sys.stderr)
        if hasattr(e, "response") and e.response is not None:
            print(f"  Status: {e.response.status_code}", file=sys.stderr)
            print(f"  Response: {e.response.text[:500]}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"✗ Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

