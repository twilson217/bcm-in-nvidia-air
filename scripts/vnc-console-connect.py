#!/usr/bin/env python3
"""
Update SSH config files with VNC console forwarding for NVIDIA Air simulation nodes.

This script:
1. Lists all simulations accessible via the configured .env credentials
2. Matches simulation names to SSH config files in .ssh/
3. Verifies the simulation ID in the config matches the API
4. Adds host entries with LocalForward for VNC console access

Usage:
  python scripts/vnc-console-connect.py
  python scripts/vnc-console-connect.py --env .env.internal
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]
SSH_DIR = REPO_ROOT / ".ssh"


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


def get_all_simulations(api_url: str, jwt: str) -> List[dict]:
    """Get all simulations."""
    list_url = f"{api_url.rstrip('/')}/api/v2/simulations/"
    resp = requests.get(
        list_url,
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", data) if isinstance(data, dict) else data


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


def parse_console_url(console_url: str) -> Tuple[str, str, int]:
    """
    Parse console_url to extract worker hostname, internal IP, and port.
    
    Format: wss://worker38.air-inside.nvidia.com/192.168.82.138:18089
    Returns: (worker_hostname, internal_ip, port)
    """
    parsed = urlparse(console_url)
    worker_hostname = parsed.netloc
    
    path = parsed.path.lstrip("/")
    if ":" in path:
        internal_ip, port_str = path.rsplit(":", 1)
        port = int(port_str)
    else:
        internal_ip = path
        port = 0
    
    return worker_hostname, internal_ip, port


def parse_ssh_config(config_path: Path) -> Tuple[Optional[str], Optional[str], str]:
    """
    Parse SSH config file to extract simulation name and ID from comments.
    
    Returns: (sim_name, sim_id, full_content)
    """
    if not config_path.exists():
        return None, None, ""
    
    content = config_path.read_text(encoding="utf-8")
    sim_name = None
    sim_id = None
    
    for line in content.splitlines():
        # Look for: # Simulation: <name>
        if line.startswith("# Simulation:"):
            sim_name = line.split(":", 1)[1].strip()
        # Look for: # Simulation ID: <uuid>
        elif line.startswith("# Simulation ID:"):
            sim_id = line.split(":", 1)[1].strip()
    
    return sim_name, sim_id, content


def find_bcm_host_config(content: str) -> Optional[dict]:
    """
    Extract the BCM host connection details from the SSH config.
    
    Returns dict with: hostname, port, user, identity_file
    """
    # Look for the air-bcm-01 or bcm host entry
    pattern = r"Host\s+(?:air-bcm-01|bcm)\s*\n((?:\s+\S+.*\n)*)"
    match = re.search(pattern, content, re.IGNORECASE)
    
    if not match:
        return None
    
    block = match.group(1)
    config = {}
    
    for line in block.splitlines():
        line = line.strip()
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        key, value = parts[0].lower(), parts[1]
        
        if key == "hostname":
            config["hostname"] = value
        elif key == "port":
            config["port"] = value
        elif key == "user":
            config["user"] = value
        elif key == "identityfile":
            config["identity_file"] = value
    
    return config if config.get("hostname") else None


def remove_vnc_host_entries(content: str) -> str:
    """
    Remove any existing VNC console host entries (those with LocalForward).
    Also removes VNC header comments.
    Preserves the header and main SSH entries (air-bcm-01, bcm).
    """
    lines = content.splitlines()
    result = []
    skip_block = False
    
    for i, line in enumerate(lines):
        # Skip VNC auto-generated header comments
        if line.strip().startswith("# VNC Console Hosts"):
            continue
        if line.strip().startswith("# Usage: ssh -F .ssh/"):
            continue
        if line.strip().startswith("#        Then connect VNC"):
            continue
        
        # Check if this is a Host line
        if line.strip().startswith("Host "):
            host_name = line.split(None, 1)[1].strip() if len(line.split(None, 1)) > 1 else ""
            # Keep air-bcm-01 and bcm entries
            if host_name in ("air-bcm-01", "bcm"):
                skip_block = False
                result.append(line)
            else:
                # Check if the next lines contain LocalForward (VNC entry)
                # Look ahead to see if this block has LocalForward
                has_localforward = False
                for j in range(i + 1, min(i + 10, len(lines))):
                    if lines[j].strip().startswith("Host "):
                        break
                    if "LocalForward" in lines[j]:
                        has_localforward = True
                        break
                
                if has_localforward:
                    skip_block = True  # Skip this entire block
                else:
                    skip_block = False
                    result.append(line)
        elif skip_block:
            # Skip lines in a VNC block
            continue
        else:
            result.append(line)
    
    # Clean up trailing newlines and ensure we end with one
    while result and result[-1] == "":
        result.pop()
    
    return "\n".join(result) + "\n" if result else ""


def generate_vnc_host_entry(
    node_name: str,
    worker_hostname: str,
    ssh_port: str,
    user: str,
    identity_file: str,
    internal_ip: str,
    console_port: int,
) -> str:
    """Generate an SSH config host entry for VNC console access."""
    return f"""Host {node_name}
  HostName {worker_hostname}
  Port {ssh_port}
  User {user}
  PreferredAuthentications publickey,password
  IdentityFile {identity_file}
  StrictHostKeyChecking no
  UserKnownHostsFile /dev/null
  LocalForward {console_port} {internal_ip}:{console_port}
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Update SSH config files with VNC console forwarding for NVIDIA Air nodes.",
    )
    
    parser.add_argument(
        "--env",
        default=str(REPO_ROOT / ".env"),
        help="Path to env file (default: .env)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    
    args = parser.parse_args()
    
    # Load env file
    env_path = Path(args.env)
    if not env_path.exists():
        for alt in [REPO_ROOT / ".env.internal", REPO_ROOT / ".env.external"]:
            if alt.exists():
                env_path = alt
                break
        else:
            print("✗ No env file found", file=sys.stderr)
            return 1
    
    env = parse_dotenv(env_path)
    
    username = env.get("AIR_USERNAME")
    api_token = env.get("AIR_API_TOKEN")
    
    if not username or not api_token:
        print("✗ Missing AIR_USERNAME or AIR_API_TOKEN in env file", file=sys.stderr)
        return 1
    
    api_url = env.get("AIR_API_URL", "https://air.nvidia.com")
    
    print(f"Using API: {api_url}")
    print(f"Using env: {env_path.name}")
    print()
    
    # Check if .ssh directory exists
    if not SSH_DIR.exists():
        print(f"✗ SSH config directory not found: {SSH_DIR}", file=sys.stderr)
        return 1
    
    try:
        # Authenticate
        print("Authenticating...")
        jwt = air_login(api_url, username, api_token)
        print("✓ Authenticated")
        print()
        
        # Get all simulations
        print("Fetching simulations...")
        simulations = get_all_simulations(api_url, jwt)
        print(f"✓ Found {len(simulations)} simulation(s)")
        print()
        
        # Build a map of simulation name -> simulation info
        sim_by_name: Dict[str, dict] = {}
        for sim in simulations:
            title = sim.get("title") or sim.get("name")
            if title:
                sim_by_name[title] = sim
        
        # Scan SSH config files
        updated_configs = []
        
        for config_file in SSH_DIR.iterdir():
            if config_file.is_dir() or config_file.name.startswith("."):
                continue
            
            config_name = config_file.name
            
            # Check if filename matches a simulation name
            if config_name not in sim_by_name:
                print(f"⊘ {config_name}: No matching simulation found")
                continue
            
            sim = sim_by_name[config_name]
            sim_id = sim.get("id")
            sim_state = sim.get("state", "unknown")
            
            # Parse the SSH config to verify simulation ID
            file_sim_name, file_sim_id, content = parse_ssh_config(config_file)
            
            if file_sim_id and file_sim_id != sim_id:
                print(f"⚠ {config_name}: Simulation ID mismatch!")
                print(f"   Config file has: {file_sim_id}")
                print(f"   API reports:     {sim_id}")
                continue
            
            print(f"✓ {config_name}: Matched simulation (state: {sim_state})")
            
            # Get BCM host connection details from config
            bcm_config = find_bcm_host_config(content)
            if not bcm_config:
                print(f"  ⚠ Could not find air-bcm-01/bcm host entry in config")
                continue
            
            # Get nodes for this simulation
            nodes = get_simulation_nodes(api_url, jwt, sim_id)
            if not nodes:
                print(f"  ⚠ No nodes found for simulation")
                continue
            
            # Sort nodes by name
            nodes.sort(key=lambda n: n.get("name", ""))
            
            # Remove existing VNC host entries
            clean_content = remove_vnc_host_entries(content)
            
            # Generate new VNC host entries
            vnc_entries = []
            vnc_entries.append(f"# VNC Console Hosts (auto-generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
            vnc_entries.append("# Usage: ssh -F .ssh/<sim-name> <host-name>")
            vnc_entries.append("#        Then connect VNC client to localhost:<port>")
            vnc_entries.append("")
            
            node_entries = []
            for node in nodes:
                name = node.get("name", "unknown")
                console_url = node.get("console_url", "")
                console_port = node.get("console_port")
                console_password = node.get("console_password", "")
                
                if not console_url or not console_port:
                    continue
                
                worker_hostname, internal_ip, _ = parse_console_url(console_url)
                
                entry = generate_vnc_host_entry(
                    node_name=name,
                    worker_hostname=bcm_config["hostname"],
                    ssh_port=bcm_config["port"],
                    user=bcm_config.get("user", "ubuntu"),
                    identity_file=bcm_config.get("identity_file", "~/.ssh/id_rsa"),
                    internal_ip=internal_ip,
                    console_port=console_port,
                )
                vnc_entries.append(entry)
                node_entries.append((name, console_port, console_password))
            
            if not node_entries:
                print(f"  ⚠ No nodes with VNC console available")
                continue
            
            # Combine content
            new_content = clean_content + "\n" + "\n".join(vnc_entries)
            
            if args.dry_run:
                print(f"  Would add {len(node_entries)} VNC host entries")
                for name, port, _ in node_entries:
                    print(f"    - {name} (port {port})")
            else:
                config_file.write_text(new_content, encoding="utf-8")
                print(f"  ✓ Added {len(node_entries)} VNC host entries")
                updated_configs.append((config_name, node_entries))
        
        print()
        
        # Print usage instructions
        if updated_configs and not args.dry_run:
            print("=" * 70)
            print("USAGE")
            print("=" * 70)
            print()
            for config_name, node_entries in updated_configs:
                print(f"To connect to VNC consoles in simulation '{config_name}':")
                print()
                for name, port, password in node_entries[:3]:  # Show first 3 as examples
                    print(f"  ssh -F .ssh/{config_name} {name}")
                    print(f"  # Then connect VNC to localhost:{port}")
                    print(f"  # VNC Password: {password}")
                    print()
                if len(node_entries) > 3:
                    print(f"  ... and {len(node_entries) - 3} more hosts")
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
