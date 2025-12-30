#!/usr/bin/env python3
"""
Get detailed information about an NVIDIA Air simulation.

Usage:
  python scripts/air-tests/get_sim_info.py --sim-id <uuid>
  python scripts/air-tests/get_sim_info.py --sim-name <name>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Optional

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


def air_login(api_url: str, username: str, api_token: str) -> str:
    """Login to Air API and return JWT token."""
    login_url = f"{api_url.rstrip('/')}/api/v1/login/"
    print(f"Logging in to {login_url} as {username}...")
    resp = requests.post(
        login_url,
        data={"username": username, "password": api_token},
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json().get("token")
    if not token:
        raise RuntimeError(f"No token in response: {resp.json()}")
    print(f"✓ Authenticated\n")
    return token


def find_simulation_by_name(api_url: str, jwt: str, name: str) -> Optional[str]:
    """Find simulation ID by name."""
    list_url = f"{api_url.rstrip('/')}/api/v2/simulations/"
    print(f"Searching for simulation named '{name}'...")
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
            sim_id = sim.get("id")
            print(f"✓ Found: {name} → {sim_id}\n")
            return sim_id
    
    print(f"✗ No simulation found with name '{name}'")
    return None


def get_simulation_details(api_url: str, jwt: str, sim_id: str) -> dict:
    """Get simulation details."""
    url = f"{api_url.rstrip('/')}/api/v2/simulations/{sim_id}/"
    print(f"GET {url}")
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def get_simulation_nodes(api_url: str, jwt: str, sim_id: str) -> list:
    """Get simulation nodes."""
    url = f"{api_url.rstrip('/')}/api/v2/simulations/{sim_id}/nodes/"
    print(f"GET {url}")
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", data) if isinstance(data, dict) else data


def get_simulation_interfaces(api_url: str, jwt: str, sim_id: str) -> list:
    """Get simulation interfaces."""
    url = f"{api_url.rstrip('/')}/api/v2/simulations/{sim_id}/interfaces/"
    print(f"GET {url}")
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=30,
    )
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", data) if isinstance(data, dict) else data


def get_simulation_services(api_url: str, jwt: str, sim_id: str) -> list:
    """Get simulation services."""
    url = f"{api_url.rstrip('/')}/api/v2/simulations/{sim_id}/services/"
    print(f"GET {url}")
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=30,
    )
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", data) if isinstance(data, dict) else data


def get_simulation_jobs(api_url: str, jwt: str, sim_id: str) -> list:
    """Get simulation jobs/operations."""
    url = f"{api_url.rstrip('/')}/api/v2/simulations/{sim_id}/jobs/"
    print(f"GET {url}")
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=30,
    )
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", data) if isinstance(data, dict) else data


def get_simulation_events(api_url: str, jwt: str, sim_id: str) -> list:
    """Get simulation events/logs."""
    url = f"{api_url.rstrip('/')}/api/v2/simulations/{sim_id}/events/"
    print(f"GET {url}")
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=30,
    )
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", data) if isinstance(data, dict) else data


def try_start_simulation(api_url: str, jwt: str, sim_id: str) -> dict:
    """Try to start the simulation and capture any error."""
    url = f"{api_url.rstrip('/')}/api/v2/simulations/{sim_id}/control/"
    print(f"\nAttempting to start simulation...")
    print(f"POST {url}")
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {jwt}"},
        json={"action": "start"},
        timeout=60,
    )
    return {
        "status_code": resp.status_code,
        "headers": dict(resp.headers),
        "body": resp.text,
        "json": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Get detailed information about an NVIDIA Air simulation.",
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
        "--try-start",
        action="store_true",
        help="Attempt to start the simulation and show any error",
    )
    
    args = parser.parse_args()
    
    # Load env file
    env_path = Path(args.env)
    if not env_path.exists():
        print(f"✗ Env file not found: {env_path}", file=sys.stderr)
        return 1
    
    env = parse_dotenv(env_path)
    print(f"Using env file: {env_path}")
    
    # Get credentials
    username = env.get("AIR_USERNAME")
    api_token = env.get("AIR_API_TOKEN")
    
    if not username or not api_token:
        print("✗ Missing AIR_USERNAME or AIR_API_TOKEN in env file", file=sys.stderr)
        return 1
    
    api_url = env.get("AIR_API_URL", "https://air.nvidia.com")
    print(f"API URL: {api_url}\n")
    
    try:
        # Authenticate
        jwt = air_login(api_url, username, api_token)
        
        # Resolve simulation ID
        if args.sim_name:
            sim_id = find_simulation_by_name(api_url, jwt, args.sim_name)
            if not sim_id:
                return 1
        else:
            sim_id = args.sim_id
        
        # Get simulation details
        print("\n" + "=" * 60)
        print("SIMULATION DETAILS")
        print("=" * 60)
        sim = get_simulation_details(api_url, jwt, sim_id)
        
        # Key fields to highlight
        key_fields = [
            "id", "title", "state", "state_message", "error", "error_message",
            "created", "updated", "expires", "sleep", "loaded",
            "organization", "owner", "documentation",
        ]
        
        print("\nKey Fields:")
        for field in key_fields:
            if field in sim:
                value = sim[field]
                if field in ("state", "error", "state_message", "error_message") and value:
                    print(f"  {field}: {value}  <<<")
                else:
                    print(f"  {field}: {value}")
        
        print("\nFull Response:")
        print(json.dumps(sim, indent=2, default=str))
        
        # Get nodes
        print("\n" + "=" * 60)
        print("SIMULATION NODES")
        print("=" * 60)
        nodes = get_simulation_nodes(api_url, jwt, sim_id)
        for node in nodes:
            state = node.get("state", "unknown")
            name = node.get("name", "unnamed")
            error = node.get("error") or node.get("error_message") or ""
            print(f"  - {name}: state={state}" + (f" error={error}" if error else ""))
        
        # Get services
        print("\n" + "=" * 60)
        print("SIMULATION SERVICES")
        print("=" * 60)
        services = get_simulation_services(api_url, jwt, sim_id)
        if services:
            for svc in services:
                print(f"  - {svc.get('name', 'unnamed')}: {svc.get('state', 'unknown')}")
        else:
            print("  (none or endpoint not available)")
        
        # Get jobs
        print("\n" + "=" * 60)
        print("SIMULATION JOBS")
        print("=" * 60)
        jobs = get_simulation_jobs(api_url, jwt, sim_id)
        if jobs:
            for job in jobs:
                print(f"  - {job}")
        else:
            print("  (none or endpoint not available)")
        
        # Get events
        print("\n" + "=" * 60)
        print("SIMULATION EVENTS")
        print("=" * 60)
        events = get_simulation_events(api_url, jwt, sim_id)
        if events:
            for event in events[:20]:  # Show last 20
                print(f"  - {event}")
        else:
            print("  (none or endpoint not available)")
        
        # Try to start if requested
        if args.try_start:
            print("\n" + "=" * 60)
            print("ATTEMPTING START")
            print("=" * 60)
            result = try_start_simulation(api_url, jwt, sim_id)
            print(f"\nStatus Code: {result['status_code']}")
            print(f"\nResponse Body:")
            if result['json']:
                print(json.dumps(result['json'], indent=2, default=str))
            else:
                print(result['body'][:2000])
        
        return 0
        
    except requests.exceptions.HTTPError as e:
        print(f"✗ HTTP error: {e}", file=sys.stderr)
        if hasattr(e, "response") and e.response is not None:
            print(f"  Status: {e.response.status_code}", file=sys.stderr)
            print(f"  Response: {e.response.text[:1000]}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"✗ Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

