#!/usr/bin/env python3
"""
Reset (power-cycle) an NVIDIA Air simulation node/device by name.

Usage:
  ./air-reset-device.py --device-name cpu-01
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests
from requests import Response


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
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
            v = v[1:-1]
        env[k] = v
    return env


def _maybe_set_proxy_env(env: Dict[str, str]) -> None:
    """
    If the user put proxy settings in .env, honor them by exporting to the process
    env if not already set.
    """
    for k in ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy"):
        v = env.get(k)
        if v and k not in os.environ:
            os.environ[k] = v


def _request_with_retries(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    data: Optional[dict] = None,
    json_body: Optional[dict] = None,
    timeout_s: int = 30,
    retries: int = 0,
) -> Response:
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            return requests.request(
                method,
                url,
                headers=headers,
                data=data,
                json=json_body,
                timeout=timeout_s,
            )
        except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
            last_exc = e
            if attempt >= retries:
                raise
    assert last_exc is not None
    raise last_exc


def air_login(api_url: str, username: str, api_token: str) -> str:
    """Login to Air API and return bearer token."""
    login_url = f"{api_url.rstrip('/')}/api/v1/login/"
    resp = _request_with_retries(
        "POST",
        login_url,
        data={"username": username, "password": api_token},
        timeout_s=30,
        retries=0,
    )
    resp.raise_for_status()
    token = resp.json().get("token")
    if not token:
        raise RuntimeError(f"No token in response: {resp.json()}")
    return token


def _get_all_pages(url: str, headers: Dict[str, str], *, timeout_s: int = 30) -> List[dict]:
    items: List[dict] = []
    next_url: Optional[str] = url
    while next_url:
        resp = _request_with_retries("GET", next_url, headers=headers, timeout_s=timeout_s, retries=0)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "results" in data:
            items.extend(data.get("results") or [])
            next_url = data.get("next")
        elif isinstance(data, list):
            items.extend(data)
            next_url = None
        else:
            raise RuntimeError(f"Unexpected response from {next_url}: {data}")
    return items


def list_simulation_nodes(api_url: str, jwt: str, sim_id: str) -> List[dict]:
    # Per API spec, v2 node listing is a global endpoint filtered by simulation UUID:
    # GET /api/v2/simulations/nodes/?simulation=<uuid>
    url = f"{api_url.rstrip('/')}/api/v2/simulations/nodes/?simulation={sim_id}"
    return _get_all_pages(url, headers={"Authorization": f"Bearer {jwt}"})


def reset_node(api_url: str, jwt: str, node_id: str) -> dict:
    """
    POST /api/v2/simulations/nodes/{id}/control/
    Payload schema: SimNodeAction { action: reset|rebuild }
    """
    url = f"{api_url.rstrip('/')}/api/v2/simulations/nodes/{node_id}/control/"
    resp = _request_with_retries(
        "POST",
        url,
        headers={"Authorization": f"Bearer {jwt}"},
        json_body={"action": "reset"},
        timeout_s=60,
        retries=0,
    )
    resp.raise_for_status()
    return resp.json()


def main(argv: Optional[List[str]] = None) -> int:
    repo_root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Reset an NVIDIA Air device/node by name.")
    parser.add_argument("--device-name", required=True, help="Device/node name (e.g. cpu-01)")
    parser.add_argument(
        "--env",
        default=str(repo_root / ".env"),
        help="Path to .env file (default: ./.env)",
    )
    parser.add_argument(
        "--sim-id",
        help="Simulation UUID (default: SIM_ID from env file)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="HTTP timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=1,
        help="Retry count for transient network failures (default: 1)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Show full tracebacks on errors",
    )
    args = parser.parse_args(argv)

    env_path = Path(args.env)
    if not env_path.exists():
        print(f"✗ Env file not found: {env_path}", file=sys.stderr)
        return 1

    env = parse_dotenv(env_path)
    _maybe_set_proxy_env(env)
    api_url = env.get("AIR_API_URL", "https://air.nvidia.com")
    username = env.get("AIR_USERNAME")
    api_token = env.get("AIR_API_TOKEN")
    sim_id = args.sim_id or env.get("SIM_ID")

    missing = [k for k, v in [("AIR_USERNAME", username), ("AIR_API_TOKEN", api_token), ("SIM_ID", sim_id)] if not v]
    if missing:
        print(f"✗ Missing required env var(s) in {env_path}: {', '.join(missing)}", file=sys.stderr)
        return 1

    try:
        # Auth
        login_url = f"{api_url.rstrip('/')}/api/v1/login/"
        resp = _request_with_retries(
            "POST",
            login_url,
            data={"username": username, "password": api_token},  # type: ignore[arg-type]
            timeout_s=args.timeout,
            retries=args.retries,
        )
        resp.raise_for_status()
        jwt = resp.json().get("token")
        if not jwt:
            raise RuntimeError(f"No token in response: {resp.json()}")

        # Nodes (filter by simulation; optionally filter by name server-side if supported)
        base_nodes_url = f"{api_url.rstrip('/')}/api/v2/simulations/nodes/?simulation={sim_id}"  # type: ignore[arg-type]
        nodes_url = f"{base_nodes_url}&name={requests.utils.quote(args.device_name)}"
        nodes = _get_all_pages(
            nodes_url,
            headers={"Authorization": f"Bearer {jwt}"},
            timeout_s=args.timeout,
        )
        matches = [n for n in nodes if n.get("name") == args.device_name]

        if not matches:
            available = sorted({n.get("name") for n in nodes if n.get("name")})
            print(f"✗ No node found with name '{args.device_name}' in sim {sim_id}", file=sys.stderr)
            if available:
                print("Available node names:", file=sys.stderr)
                for n in available:
                    print(f"  - {n}", file=sys.stderr)
            return 1

        if len(matches) > 1:
            print(f"✗ Multiple nodes matched name '{args.device_name}' (unexpected).", file=sys.stderr)
            print(json.dumps(matches, indent=2, default=str), file=sys.stderr)
            return 1

        node = matches[0]
        node_id = node.get("id")
        if not node_id:
            print(f"✗ Matched node missing 'id' field: {node}", file=sys.stderr)
            return 1

        print(f"Resetting node '{args.device_name}' (node_id={node_id}) in sim {sim_id}...")
        reset_url = f"{api_url.rstrip('/')}/api/v2/simulations/nodes/{node_id}/control/"
        reset_resp = _request_with_retries(
            "POST",
            reset_url,
            headers={"Authorization": f"Bearer {jwt}"},
            json_body={"action": "reset"},
            timeout_s=max(args.timeout, 60),
            retries=args.retries,
        )
        reset_resp.raise_for_status()
        result = reset_resp.json()
        print("✓ Reset requested successfully")
        print(json.dumps(result, indent=2, default=str))
        return 0

    except requests.exceptions.HTTPError as e:
        print(f"✗ HTTP error: {e}", file=sys.stderr)
        if hasattr(e, "response") and e.response is not None:
            print(f"  Status: {e.response.status_code}", file=sys.stderr)
            ct = e.response.headers.get("content-type", "")
            body = e.response.text
            if ct.startswith("application/json"):
                try:
                    print(json.dumps(e.response.json(), indent=2, default=str)[:4000], file=sys.stderr)
                except Exception:
                    print(body[:2000], file=sys.stderr)
            else:
                print(body[:2000], file=sys.stderr)
        return 1
    except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError) as e:
        parsed = urlparse(api_url)
        host = parsed.hostname or api_url
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        print(
            f"✗ Network error: cannot connect to {host}:{port} (from AIR_API_URL={api_url}).",
            file=sys.stderr,
        )
        print(f"  Details: {e}", file=sys.stderr)
        print(
            "  This is usually routing/firewall/VPN/proxy-related from the machine you're running on.",
            file=sys.stderr,
        )
        print(
            f"  Quick test: curl -I -m 10 {api_url.rstrip('/')}/api/v1/login/",
            file=sys.stderr,
        )
        print(
            "  If your environment requires a proxy, set HTTPS_PROXY/HTTP_PROXY (you can put them in your .env).",
            file=sys.stderr,
        )
        if args.debug:
            import traceback

            traceback.print_exc()
        return 1
    except Exception as e:
        print(f"✗ Error: {e}", file=sys.stderr)
        if args.debug:
            import traceback

            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


