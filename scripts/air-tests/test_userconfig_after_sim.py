#!/usr/bin/env python3
"""
Test UserConfig creation immediately after simulation creation.
This mimics what deploy_bcm_air.py does to see if the 403 is timing-related.
"""

import os
import sys
import time
import argparse
import json
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

REPO_ROOT = Path(__file__).resolve().parents[2]


def _api_base(url: str) -> str:
    url = url.rstrip("/")
    if url.endswith("/api/v2"):
        url = url[:-7]
    if url.endswith("/api/v1"):
        url = url[:-7]
    return url


def _wait_for_state(base_url: str, headers: dict, sim_id: str, timeout_s: int = 300) -> str:
    start = time.time()
    last = None
    while time.time() - start < timeout_s:
        r = requests.get(f"{base_url}/api/v2/simulations/{sim_id}/", headers=headers, timeout=30)
        if r.status_code != 200:
            time.sleep(2)
            continue
        state = (r.json() or {}).get("state")
        if state != last:
            print(f"  state: {state}")
            last = state
        if state in ("LOADED", "ERROR", "FAILED"):
            return state
        time.sleep(2)
    return "TIMEOUT"


def _assign_userconfig_via_sdk(api_url: str, username: str, api_token: str, sim_id: str, userconfig_id: str) -> None:
    # Match deploy_bcm_air.py behavior: use air-sdk and call node.set_cloud_init_assignment({'user_data': <id>})
    try:
        from air_sdk import AirApi  # type: ignore
    except Exception as e:
        raise RuntimeError(f"air_sdk not available: {e}")

    air = AirApi(username=username, password=api_token, api_url=_api_base(api_url))
    nodes = air.simulation_nodes.list(simulation=sim_id)
    nodes = list(nodes)
    print(f"Assigning UserConfig to nodes via SDK (count={len(nodes)})...")
    for node in nodes:
        node_name = getattr(node, "name", "<unknown>")
        node.set_cloud_init_assignment({"user_data": userconfig_id})
        print(f"  ✓ assigned to {node_name}")


def _list_simulation_nodes(base_url: str, headers: dict, sim_id: str) -> list[dict]:
    """
    List simulation nodes via REST. Returns list of dicts with at least: id, name.
    Uses the OpenAPI-defined endpoint:
      GET /api/v2/simulations/nodes/?simulation=<uuid>
    """
    r = requests.get(
        f"{base_url}/api/v2/simulations/nodes/",
        headers=headers,
        params={"simulation": sim_id},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Failed to list simulation nodes: {r.status_code} {r.text[:300]}")
    data = r.json()
    if isinstance(data, list):
        return data
    return data.get("results", [])


def _get_cloud_init_assignment(base_url: str, headers: dict, sim_node_id: str) -> dict:
    """
    Get cloud-init assignment for a simulation node via REST:
      GET /api/v2/simulations/nodes/{id}/cloud-init/
    """
    r = requests.get(
        f"{base_url}/api/v2/simulations/nodes/{sim_node_id}/cloud-init/",
        headers=headers,
        timeout=30,
    )
    if r.status_code != 200:
        return {"_error": f"{r.status_code}", "_body": (r.text or "")[:500]}
    return r.json() if r.headers.get("content-type", "").startswith("application/json") else {"_body": (r.text or "")[:500]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Test UserConfig creation after simulation creation")
    parser.add_argument("--delete", action="store_true", help="Delete the simulation at the end")
    parser.add_argument("--title", default="test-userconfig-timing", help="Simulation title (default: test-userconfig-timing)")
    parser.add_argument(
        "--topology",
        default=str(REPO_ROOT / "topologies" / "default" / "topology.json"),
        help="Path to a JSON topology export to import (default: topologies/default/topology.json)",
    )
    parser.add_argument(
        "--sim-id",
        help="Use an existing simulation ID. If provided, the script will NOT create a new simulation.",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="Read-only mode. Requires --sim-id. Shows which cloud-init UserConfigs are assigned to which nodes.",
    )
    args = parser.parse_args()

    base_url = _api_base(API_URL)
    print(f"API URL: {base_url}")
    print(f"Username: {USERNAME}")
    print()

    # Step 1: Login
    print("=" * 60)
    print("Step 1: Login")
    print("=" * 60)
    resp = requests.post(f"{base_url}/api/v1/login/", data={
        "username": USERNAME,
        "password": API_TOKEN,
    })
    if resp.status_code != 200:
        print(f"Login failed: {resp.text}")
        return 1
    jwt = resp.json().get("token")
    if not jwt:
        print("Login succeeded but no token returned")
        return 1
    print("✓ JWT obtained")

    headers = {
        "Authorization": f"Bearer {jwt}",
        "Content-Type": "application/json",
    }

    # Validation for new modes
    if args.read_only and not args.sim_id:
        print("ERROR: --read-only requires --sim-id")
        return 2
    if args.read_only and args.delete:
        print("ERROR: --read-only cannot be combined with --delete")
        return 2
    if args.sim_id and args.delete:
        print("ERROR: --delete is only supported for simulations created by this script (omit --sim-id)")
        return 2

    sim_id = None
    created_sim = False

    if args.sim_id:
        sim_id = args.sim_id.strip()
        print("\n" + "=" * 60)
        print("Step 2: Using existing simulation")
        print("=" * 60)
        print(f"Simulation ID: {sim_id}")
    else:
        # Step 2: Create a simulation from topology.json (same import flow as deploy_bcm_air.py)
        print("\n" + "=" * 60)
        print("Step 2: Create simulation (import topology.json)")
        print("=" * 60)

        topology_path = Path(args.topology)
        if topology_path.is_dir():
            topology_path = topology_path / "topology.json"
        if not topology_path.exists():
            print(f"✗ Topology not found: {topology_path}")
            return 1
        if topology_path.suffix.lower() != ".json":
            print(f"✗ Topology must be a .json export: {topology_path}")
            return 1

        topology_data = json.loads(topology_path.read_text(encoding="utf-8"))
        # Override title with CLI title for clarity in the UI
        if isinstance(topology_data, dict):
            topology_data["title"] = args.title
        else:
            print(f"✗ Unexpected topology JSON format (expected object): {topology_path}")
            return 1

        print(f"Importing topology: {topology_path}")
        resp = requests.post(
            f"{base_url}/api/v2/simulations/import/",
            headers=headers,
            json=topology_data,
            timeout=60,
        )
        print(f"Status: {resp.status_code}")
        if resp.status_code not in (200, 201):
            print(f"Failed to create simulation: {resp.text[:300]}")
            return 1
        sim_id = resp.json().get("id")
        if not sim_id:
            print("Simulation created but no id returned")
            return 1
        created_sim = True
        print(f"✓ Simulation created: {sim_id}")

    if not sim_id:
        print("ERROR: No simulation ID available")
        return 2

    # Read-only mode: show current cloud-init assignments per node
    if args.read_only:
        print("\n" + "=" * 60)
        print("Read-only: Cloud-init assignments")
        print("=" * 60)
        nodes = _list_simulation_nodes(base_url, headers, sim_id)
        print(f"Nodes found: {len(nodes)}\n")
        for n in nodes:
            nid = n.get("id")
            name = n.get("name")
            if not nid:
                print(f"- {name}: (missing id)")
                continue
            assignment = _get_cloud_init_assignment(base_url, headers, nid)
            # Common fields are usually: user_data / meta_data (depending on API serializer)
            user_data = assignment.get("user_data") if isinstance(assignment, dict) else None
            meta_data = assignment.get("meta_data") if isinstance(assignment, dict) else None
            if isinstance(assignment, dict) and assignment.get("_error"):
                print(f"- {name}: ERROR {assignment.get('_error')} {assignment.get('_body','')}")
            else:
                print(f"- {name}: user_data={user_data} meta_data={meta_data}")
        return 0

    # Step 3: Create UserConfig (immediately after sim)
    print("\n" + "=" * 60)
    print("Step 3: POST /api/v2/userconfigs/ (for cloud-init user_data)")
    print("=" * 60)

    cloudinit_path = REPO_ROOT / "cloud-init-password.yaml"
    if cloudinit_path.exists():
        content = cloudinit_path.read_text(encoding="utf-8")
        print(f"Using cloud-init content from {cloudinit_path} ({len(content)} bytes)")
    else:
        content = "#cloud-config\nusers: []\n"
        print("Using minimal test content (repo cloud-init-password.yaml not found)")

    payload = {
        "name": f"test-timing-{sim_id[:8]}",
        "kind": "cloud-init-user-data",
        "organization": None,
        "content": content,
    }

    resp = requests.post(f"{base_url}/api/v2/userconfigs/", headers=headers, json=payload)
    print(f"Status: {resp.status_code}")

    config_id = None
    if resp.status_code == 201:
        config_id = resp.json().get("id")
        print(f"✓ UserConfig created: {config_id}")
    else:
        print(f"Response: {resp.text[:500]}")

    # Step 4: Wait 5 seconds and try again if needed
    if not config_id:
        print("\n" + "=" * 60)
        print("Step 4: Wait 5s, then try UserConfig again")
        print("=" * 60)
        print("Waiting 5 seconds...")
        time.sleep(5)
        payload["name"] = f"test-timing-delayed-{sim_id[:8]}"
        resp = requests.post(f"{base_url}/api/v2/userconfigs/", headers=headers, json=payload)
        print(f"Status: {resp.status_code}")
        if resp.status_code == 201:
            config_id = resp.json().get("id")
            print(f"✓ UserConfig created (after delay): {config_id}")
        else:
            print(f"Response: {resp.text[:500]}")
            print("\nNo UserConfig created; leaving simulation for inspection.")
            if args.delete:
                print("\nDeleting simulation (--delete)...")
                requests.delete(f"{base_url}/api/v2/simulations/{sim_id}/", headers=headers)
            return 2

    # Step 5: Assign user_data to nodes (via SDK, like deploy script)
    print("\n" + "=" * 60)
    print("Step 5: Assign UserConfig to nodes via SDK")
    print("=" * 60)
    try:
        _assign_userconfig_via_sdk(base_url, USERNAME, API_TOKEN, sim_id, config_id)
    except Exception as e:
        print(f"✗ Failed to assign UserConfig via SDK: {e}")
        print("\nLeaving simulation for inspection.")
        if args.delete:
            print("\nDeleting simulation (--delete)...")
            requests.delete(f"{base_url}/api/v2/simulations/{sim_id}/", headers=headers)
        return 3

    # NOTE: Do NOT start/load the simulation by default.
    # This script is intended to isolate whether cloud-init assignment itself triggers backend issues.
    state = "NOT_STARTED"

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"Simulation ID: {sim_id}")
    print(f"UserConfig ID: {config_id}")
    print(f"Final state: {state}")
    if not args.delete:
        print("Simulation was NOT deleted (default). Use --delete to delete it at the end.")
        print("Simulation was NOT started (by design). You can start it manually in the UI or via API if needed.")

    if args.delete:
        print("\nDeleting simulation (--delete)...")
        dr = requests.delete(f"{base_url}/api/v2/simulations/{sim_id}/", headers=headers)
        print(f"Delete status: {dr.status_code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

