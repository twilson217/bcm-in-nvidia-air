#!/usr/bin/env python3
"""
Delete an NVIDIA Air simulation by ID or name.

Usage:
  python scripts/delete-sim.py --sim-id <uuid>
  python scripts/delete-sim.py --sim-name <name>
  python scripts/delete-sim.py --sim-id <uuid> --env .env.external
  python scripts/delete-sim.py --sim-id <uuid> --internal  # shortcut for air-inside
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, Optional

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
    print(f"  Logging in to {login_url} as {username}...")
    resp = requests.post(
        login_url,
        data={"username": username, "password": api_token},
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json().get("token")
    if not token:
        raise RuntimeError(f"No token in response: {resp.json()}")
    print(f"  ✓ Authenticated")
    return token


def find_simulation_by_name(api_url: str, jwt: str, name: str) -> Optional[str]:
    """Find simulation ID by name."""
    list_url = f"{api_url.rstrip('/')}/api/v2/simulations/"
    print(f"  Searching for simulation named '{name}'...")
    resp = requests.get(
        list_url,
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    
    # Handle paginated response
    results = data.get("results", data) if isinstance(data, dict) else data
    
    for sim in results:
        if sim.get("title") == name or sim.get("name") == name:
            sim_id = sim.get("id")
            print(f"  ✓ Found: {name} → {sim_id}")
            return sim_id
    
    print(f"  ✗ No simulation found with name '{name}'")
    return None


def delete_simulation(api_url: str, jwt: str, sim_id: str) -> bool:
    """Delete simulation by ID."""
    delete_url = f"{api_url.rstrip('/')}/api/v2/simulations/{sim_id}/"
    print(f"  Deleting simulation {sim_id}...")
    print(f"  DELETE {delete_url}")
    
    resp = requests.delete(
        delete_url,
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=60,
    )
    
    if resp.status_code in (200, 202, 204):
        print(f"  ✓ Deleted successfully (status={resp.status_code})")
        return True
    else:
        print(f"  ✗ Delete failed (status={resp.status_code})")
        print(f"  Response: {resp.text[:500]}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete an NVIDIA Air simulation by ID or name.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/delete-sim.py --sim-id 16514465-7187-432a-9beb-b3f88556a01a
  python scripts/delete-sim.py --sim-name "202512001-BCM-Lab"
  python scripts/delete-sim.py --sim-id <uuid> --env .env.external
  python scripts/delete-sim.py --sim-id <uuid> --internal
        """,
    )
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sim-id", help="Simulation UUID to delete")
    group.add_argument("--sim-name", help="Simulation name to find and delete")
    
    parser.add_argument(
        "--env",
        default=str(REPO_ROOT / ".env"),
        help="Path to env file (default: .env)",
    )
    parser.add_argument(
        "--internal",
        action="store_true",
        help="Use air-inside.nvidia.com (default: air.nvidia.com)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting",
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
    
    # Determine API URL
    if args.internal:
        api_url = "https://air-inside.nvidia.com"
    else:
        api_url = env.get("AIR_API_URL", "https://air.nvidia.com")
    
    print(f"API URL: {api_url}")
    print()
    
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
        
        print()
        
        # Delete
        if args.dry_run:
            print(f"[DRY RUN] Would delete simulation: {sim_id}")
            return 0
        
        success = delete_simulation(api_url, jwt, sim_id)
        return 0 if success else 1
        
    except requests.exceptions.HTTPError as e:
        print(f"✗ HTTP error: {e}", file=sys.stderr)
        if hasattr(e, "response") and e.response is not None:
            print(f"  Response: {e.response.text[:500]}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"✗ Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

