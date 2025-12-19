#!/usr/bin/env python3
"""
Get VNC console connection information for NVIDIA Air simulation nodes.

This script outputs SSH tunnel commands and VNC passwords for connecting
to node consoles via VNC.

Usage:
  python scripts/vnc-console-connect.py                    # Auto-detect simulation
  python scripts/vnc-console-connect.py --sim-id <uuid>    # Specify by ID
  python scripts/vnc-console-connect.py --sim-name <name>  # Specify by name
  python scripts/vnc-console-connect.py --ssh-config       # Update SSH config file
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


def find_simulation_by_name(api_url: str, jwt: str, name: str) -> Optional[str]:
    """Find simulation ID by name."""
    simulations = get_all_simulations(api_url, jwt)
    for sim in simulations:
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


def get_simulation_services(api_url: str, jwt: str, sim_id: str) -> List[dict]:
    """Get services for a simulation."""
    url = f"{api_url.rstrip('/')}/api/v2/simulations/services/?simulation={sim_id}"
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
        if line.startswith("# Simulation:"):
            sim_name = line.split(":", 1)[1].strip()
        elif line.startswith("# Simulation ID:"):
            sim_id = line.split(":", 1)[1].strip()
    
    return sim_name, sim_id, content


def find_bcm_host_config(content: str) -> Optional[dict]:
    """
    Extract the BCM host connection details from the SSH config.
    
    Returns dict with: hostname, port, user, identity_file
    """
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
    Also removes VNC header comments and VNC password comments.
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
        # Skip VNC password comments (format: # VNC Password: ...)
        if line.strip().startswith("# VNC Password:"):
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
                has_localforward = False
                for j in range(i + 1, min(i + 10, len(lines))):
                    if lines[j].strip().startswith("Host "):
                        break
                    if "LocalForward" in lines[j]:
                        has_localforward = True
                        break
                
                if has_localforward:
                    skip_block = True
                else:
                    skip_block = False
                    result.append(line)
        elif skip_block:
            continue
        else:
            result.append(line)
    
    # Clean up trailing newlines
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
    vnc_password: str,
) -> str:
    """Generate an SSH config host entry for VNC console access."""
    return f"""# VNC Password: {vnc_password}
Host {node_name}
  HostName {worker_hostname}
  Port {ssh_port}
  User {user}
  PreferredAuthentications publickey,password
  IdentityFile {identity_file}
  StrictHostKeyChecking no
  UserKnownHostsFile /dev/null
  LocalForward {console_port} {internal_ip}:{console_port}
"""


def find_ssh_service_port(api_url: str, jwt: str, sim_id: str, worker_hostname: str) -> Optional[str]:
    """
    Find the SSH service port for a simulation.
    
    Looks for an SSH service whose dest_host matches the worker_hostname.
    Returns the external port (dest_port) or None if not found.
    """
    services = get_simulation_services(api_url, jwt, sim_id)
    
    for svc in services:
        # Look for SSH services (typically name contains 'ssh' or service is on port 22)
        svc_name = svc.get("name", "").lower()
        interface = svc.get("interface", {})
        
        # Check if this is an SSH service
        if "ssh" in svc_name or svc.get("src_port") == 22:
            dest_host = svc.get("dest_host", "")
            dest_port = svc.get("dest_port")
            
            # Check if the dest_host matches our worker hostname
            if dest_host == worker_hostname and dest_port:
                return str(dest_port)
    
    return None


def match_simulation_to_ssh_config(simulations: List[dict]) -> Tuple[Optional[dict], Optional[Path], Optional[dict]]:
    """
    Try to match simulations to SSH config files in .ssh/ directory.
    
    Returns: (matched_simulation, config_path, bcm_config) or (None, None, None)
    """
    if not SSH_DIR.exists():
        return None, None, None
    
    sim_by_name: Dict[str, dict] = {}
    for sim in simulations:
        title = sim.get("title") or sim.get("name")
        if title:
            sim_by_name[title] = sim
    
    for config_file in SSH_DIR.iterdir():
        if config_file.is_dir() or config_file.name.startswith("."):
            continue
        
        config_name = config_file.name
        
        if config_name not in sim_by_name:
            continue
        
        sim = sim_by_name[config_name]
        sim_id = sim.get("id")
        
        # Parse the SSH config to verify simulation ID
        file_sim_name, file_sim_id, content = parse_ssh_config(config_file)
        
        if file_sim_id and file_sim_id != sim_id:
            continue
        
        # Get BCM host connection details
        bcm_config = find_bcm_host_config(content)
        if bcm_config:
            return sim, config_file, bcm_config
    
    return None, None, None


def prompt_user_selection(simulations: List[dict]) -> Optional[dict]:
    """
    Prompt user to select from a list of simulations.
    
    Returns selected simulation or None if cancelled.
    """
    print("\nAvailable simulations:")
    print("-" * 50)
    for i, sim in enumerate(simulations, 1):
        title = sim.get("title") or sim.get("name") or "Unnamed"
        state = sim.get("state", "unknown")
        print(f"  {i}. {title} ({state})")
    print()
    
    try:
        choice = input("Select simulation number (or 'q' to quit): ").strip()
        if choice.lower() == 'q':
            return None
        
        idx = int(choice) - 1
        if 0 <= idx < len(simulations):
            return simulations[idx]
        else:
            print("Invalid selection.")
            return None
    except (ValueError, EOFError, KeyboardInterrupt):
        return None


def print_vnc_info(
    nodes: List[dict],
    ssh_service_port: Optional[str],
    worker_hostname: Optional[str],
) -> None:
    """Print VNC connection information for all nodes."""
    print()
    print("=" * 70)
    print("VNC CONSOLE CONNECTION INFO")
    print("=" * 70)
    
    if not ssh_service_port:
        print()
        print("⚠ You must create an SSH service for your simulation before you can")
        print("  use VNC to connect to a console.")
        print()
        return
    
    for node in nodes:
        name = node.get("name", "unknown")
        console_url = node.get("console_url", "")
        console_port = node.get("console_port")
        console_password = node.get("console_password", "")
        state = node.get("state", "unknown")
        
        print()
        print(f"Host: {name}")
        print(f"  State: {state}")
        
        if not console_url or not console_port:
            print("  (No console available)")
            continue
        
        node_worker, internal_ip, _ = parse_console_url(console_url)
        display_hostname = worker_hostname or node_worker
        
        print(f"  SSH Tunnel: ssh -L {console_port}:{internal_ip}:{console_port} {display_hostname} -p {ssh_service_port}")
        print(f"  VNC Connect: localhost:{console_port}")
        print(f"  VNC Password: {console_password}")
    
    print()


def update_ssh_config(
    config_path: Path,
    nodes: List[dict],
    bcm_config: dict,
) -> int:
    """Update SSH config file with VNC host entries."""
    content = config_path.read_text(encoding="utf-8")
    
    # Remove existing VNC entries
    clean_content = remove_vnc_host_entries(content)
    
    # Generate new VNC host entries
    vnc_entries = []
    vnc_entries.append(f"# VNC Console Hosts (auto-generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
    vnc_entries.append("# Usage: ssh -F .ssh/<sim-name> <host-name>")
    vnc_entries.append("#        Then connect VNC client to localhost:<port>")
    vnc_entries.append("")
    
    node_count = 0
    for node in nodes:
        name = node.get("name", "unknown")
        console_url = node.get("console_url", "")
        console_port = node.get("console_port")
        console_password = node.get("console_password", "")
        
        if not console_url or not console_port:
            continue
        
        _, internal_ip, _ = parse_console_url(console_url)
        
        entry = generate_vnc_host_entry(
            node_name=name,
            worker_hostname=bcm_config["hostname"],
            ssh_port=bcm_config["port"],
            user=bcm_config.get("user", "ubuntu"),
            identity_file=bcm_config.get("identity_file", "~/.ssh/id_rsa"),
            internal_ip=internal_ip,
            console_port=console_port,
            vnc_password=console_password,
        )
        vnc_entries.append(entry)
        node_count += 1
    
    if node_count == 0:
        print("  ⚠ No nodes with VNC console available")
        return 0
    
    # Write updated content
    new_content = clean_content + "\n" + "\n".join(vnc_entries)
    config_path.write_text(new_content, encoding="utf-8")
    
    print(f"  ✓ Added {node_count} VNC host entries to {config_path.name}")
    print()
    print(f"Usage: ssh -F .ssh/{config_path.name} <host-name>")
    print("       Then connect VNC client to localhost:<port>")
    
    return node_count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Get VNC console connection information for NVIDIA Air simulation nodes.",
    )
    
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--sim-id", help="Simulation UUID")
    group.add_argument("--sim-name", help="Simulation name")
    
    parser.add_argument(
        "--env",
        default=str(REPO_ROOT / ".env"),
        help="Path to env file (default: .env)",
    )
    parser.add_argument(
        "--ssh-config",
        action="store_true",
        help="Update matching SSH config file with VNC host entries",
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
    
    try:
        # Authenticate
        jwt = air_login(api_url, username, api_token)
        
        sim_id: Optional[str] = None
        sim_name: Optional[str] = None
        config_path: Optional[Path] = None
        bcm_config: Optional[dict] = None
        ssh_service_port: Optional[str] = None
        worker_hostname: Optional[str] = None
        
        # If user specified sim-id or sim-name, use that
        if args.sim_id:
            sim_id = args.sim_id
        elif args.sim_name:
            sim_id = find_simulation_by_name(api_url, jwt, args.sim_name)
            if not sim_id:
                print(f"✗ Simulation not found: {args.sim_name}", file=sys.stderr)
                return 1
            sim_name = args.sim_name
        else:
            # Auto-detect simulation
            simulations = get_all_simulations(api_url, jwt)
            
            if not simulations:
                print("✗ No simulations found", file=sys.stderr)
                return 1
            
            # Try to match to SSH config files first
            matched_sim, config_path, bcm_config = match_simulation_to_ssh_config(simulations)
            
            if matched_sim:
                sim_id = matched_sim.get("id")
                sim_name = matched_sim.get("title") or matched_sim.get("name")
                print(f"✓ Matched simulation: {sim_name}")
                
                if bcm_config:
                    ssh_service_port = bcm_config.get("port")
                    worker_hostname = bcm_config.get("hostname")
            else:
                # No SSH config match, check simulation count
                if len(simulations) == 1:
                    sim = simulations[0]
                    sim_id = sim.get("id")
                    sim_name = sim.get("title") or sim.get("name")
                    print(f"✓ Using simulation: {sim_name}")
                elif len(simulations) <= 9:
                    sim = prompt_user_selection(simulations)
                    if not sim:
                        print("Cancelled.")
                        return 0
                    sim_id = sim.get("id")
                    sim_name = sim.get("title") or sim.get("name")
                else:
                    print("Oh, my... you have a lot of simulations.")
                    print("Please use the --sim-id or --sim-name options to specify.")
                    return 1
        
        if not sim_id:
            print("✗ Could not determine simulation", file=sys.stderr)
            return 1
        
        # Get nodes
        nodes = get_simulation_nodes(api_url, jwt, sim_id)
        
        if not nodes:
            print("✗ No nodes found in simulation", file=sys.stderr)
            return 1
        
        # Sort by name
        nodes.sort(key=lambda n: n.get("name", ""))
        
        # If we don't have SSH service info from config, look it up via API
        if not ssh_service_port:
            # Get worker hostname from first node with console_url
            for node in nodes:
                console_url = node.get("console_url", "")
                if console_url:
                    worker_hostname, _, _ = parse_console_url(console_url)
                    break
            
            if worker_hostname:
                ssh_service_port = find_ssh_service_port(api_url, jwt, sim_id, worker_hostname)
        
        # Handle --ssh-config mode
        if args.ssh_config:
            if not config_path:
                # Try to find matching config for this simulation
                simulations = get_all_simulations(api_url, jwt)
                for sim in simulations:
                    if sim.get("id") == sim_id:
                        sim_name = sim.get("title") or sim.get("name")
                        break
                
                if sim_name and SSH_DIR.exists():
                    potential_config = SSH_DIR / sim_name
                    if potential_config.exists():
                        _, _, content = parse_ssh_config(potential_config)
                        bcm_config = find_bcm_host_config(content)
                        if bcm_config:
                            config_path = potential_config
            
            if not config_path or not bcm_config:
                print("✗ No matching SSH config file found for this simulation", file=sys.stderr)
                print("  The --ssh-config option requires an existing SSH config file in .ssh/", file=sys.stderr)
                return 1
            
            return 0 if update_ssh_config(config_path, nodes, bcm_config) > 0 else 1
        
        # Default mode: print VNC info
        print_vnc_info(nodes, ssh_service_port, worker_hostname)
        
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
