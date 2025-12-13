#!/usr/bin/env python3
"""
Overnight test loop for deploy_bcm_air.py.

Runs a predefined matrix of deployment tests (external/internal Air x BCM versions),
captures a full combined log, writes a concise summary log, and deletes the simulation
after each test to avoid capacity issues.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = REPO_ROOT / ".logs"
DEPLOY_LOG = LOG_DIR / "deploy_bcm_air.log"
SUMMARY_LOG = LOG_DIR / "test-summary.log"
PROGRESS_JSON = LOG_DIR / "progress.json"


@dataclass(frozen=True)
class TestCase:
    key: str
    name: str
    api_url: str
    env_file: Path
    bcm_version: str


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _format_elapsed(seconds: float) -> str:
    """Format elapsed seconds as a human-readable string."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins}m {secs}s"
    else:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours}h {mins}m {secs}s"


def _ensure_log_dir() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")


def _parse_dotenv(path: Path) -> Dict[str, str]:
    """
    Minimal .env parser (KEY=VALUE).
    - Ignores blank lines and comments.
    - Supports optional leading 'export '.
    - Supports single/double quotes (no complex expansions).
    """
    env: Dict[str, str] = {}
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


def _read_progress_sim_id(progress_path: Path) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (simulation_id, simulation_name) from .logs/progress.json if present.
    """
    if not progress_path.exists():
        return None, None
    try:
        import json

        data = json.loads(progress_path.read_text(encoding="utf-8"))
        return data.get("simulation_id"), data.get("simulation_name")
    except Exception:
        return None, None


def _air_login_jwt(api_url: str, username: str, api_token: str) -> str:
    login_url = f"{api_url.rstrip('/')}/api/v1/login/"
    resp = requests.post(
        login_url,
        data={"username": username, "password": api_token},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    jwt = payload.get("token")
    if not jwt:
        raise RuntimeError(f"Login succeeded but no token returned: {payload}")
    return jwt


def _air_delete_simulation(api_url: str, jwt_token: str, simulation_id: str) -> Tuple[bool, str]:
    delete_url = f"{api_url.rstrip('/')}/api/v2/simulations/{simulation_id}/"
    resp = requests.delete(
        delete_url,
        headers={"Authorization": f"Bearer {jwt_token}"},
        timeout=60,
    )
    if resp.status_code in (200, 202, 204):
        return True, f"deleted (status={resp.status_code})"
    return False, f"delete failed (status={resp.status_code}): {resp.text[:300]}"


def _run_deploy(test: TestCase, extra_env: Dict[str, str], dry_run: bool) -> Tuple[int, Optional[str], Optional[str]]:
    cmd = [
        sys.executable,
        str(REPO_ROOT / "deploy_bcm_air.py"),
        "-y",
        "--bcm-version",
        test.bcm_version,
        "--api-url",
        test.api_url,
    ]

    header = f"[{_now()}] START {test.key}: {test.name} | api={test.api_url} | bcm={test.bcm_version} | env={test.env_file.name}"
    _append_line(DEPLOY_LOG, "")
    _append_line(DEPLOY_LOG, "=" * len(header))
    _append_line(DEPLOY_LOG, header)
    _append_line(DEPLOY_LOG, "=" * len(header))
    _append_line(DEPLOY_LOG, f"CMD: {' '.join(cmd)}")

    if dry_run:
        print(header)
        print(f"CMD: {' '.join(cmd)}")
        return 0, None, None

    proc_env = os.environ.copy()
    proc_env.update(extra_env)

    sim_id: Optional[str] = None
    sim_name: Optional[str] = None

    # Stream output to console + deploy log in real time.
    with DEPLOY_LOG.open("a", encoding="utf-8") as log_f:
        p = subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            env=proc_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert p.stdout is not None
        for line in p.stdout:
            sys.stdout.write(line)
            log_f.write(line)

            # Capture sim name/id from deploy_bcm_air.py output so cleanup works even if
            # deploy_bcm_air.py clears progress.json in non-interactive mode.
            if sim_name is None:
                m = re.search(r"^Creating simulation from JSON file:\s*(.+)\s*$", line)
                if m:
                    sim_name = m.group(1).strip()
            if sim_id is None:
                m = re.search(r"^Simulation ID:\s*([0-9a-fA-F-]{36})\s*$", line.strip())
                if m:
                    sim_id = m.group(1)
                else:
                    m2 = re.search(r"^\s*ID:\s*([0-9a-fA-F-]{36})\s*$", line)
                    if m2:
                        sim_id = m2.group(1)
        rc = p.wait()

    # Add END marker for easier log parsing
    end_marker = f"[{_now()}] END {test.key}: exit_code={rc}"
    _append_line(DEPLOY_LOG, "")
    _append_line(DEPLOY_LOG, end_marker)
    _append_line(DEPLOY_LOG, "-" * len(end_marker))
    return rc, sim_id, sim_name


def _select_tests(all_tests: List[TestCase], args: argparse.Namespace) -> List[TestCase]:
    selected_keys: List[str] = []
    for i in range(1, 7):
        if getattr(args, f"test{i}"):
            selected_keys.append(f"test{i}")
    if not selected_keys:
        return all_tests
    by_key = {t.key: t for t in all_tests}
    return [by_key[k] for k in selected_keys if k in by_key]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an overnight deployment matrix test loop.")
    for i in range(1, 7):
        parser.add_argument(f"--test{i}", action="store_true", help=f"Run only test{i}")

    parser.add_argument(
        "--env-external",
        default=str(REPO_ROOT / ".env.external"),
        help="Path to the external Air env file (default: .env.external)",
    )
    parser.add_argument(
        "--env-internal",
        default=str(REPO_ROOT / ".env.internal"),
        help="Path to the internal Air env file (default: .env.internal)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would run, but do not execute deployments or delete sims",
    )
    parser.add_argument(
        "--stop-on-fail",
        action="store_true",
        help="Stop the loop immediately if any test fails",
    )

    args = parser.parse_args()

    env_external = Path(args.env_external)
    env_internal = Path(args.env_internal)
    if not env_external.exists():
        print(f"✗ Missing external env file: {env_external}", file=sys.stderr)
        return 2
    if not env_internal.exists():
        print(f"✗ Missing internal env file: {env_internal}", file=sys.stderr)
        return 2

    all_tests: List[TestCase] = [
        TestCase(
            key="test1",
            name="BCM 10.25.03 on air.nvidia.com (.env.external)",
            api_url="https://air.nvidia.com",
            env_file=env_external,
            bcm_version="10.25.03",
        ),
        TestCase(
            key="test2",
            name="BCM 10.30.0 on air.nvidia.com (.env.external)",
            api_url="https://air.nvidia.com",
            env_file=env_external,
            bcm_version="10.30.0",
        ),
        TestCase(
            key="test3",
            name="BCM 11.0 on air.nvidia.com (.env.external)",
            api_url="https://air.nvidia.com",
            env_file=env_external,
            bcm_version="11",  # Matches bcm-11.0-ubuntu2404.iso
        ),
        TestCase(
            key="test4",
            name="BCM 10.25.03 on air-inside.nvidia.com (.env.internal)",
            api_url="https://air-inside.nvidia.com",
            env_file=env_internal,
            bcm_version="10.25.03",
        ),
        TestCase(
            key="test5",
            name="BCM 10.30.0 on air-inside.nvidia.com (.env.internal)",
            api_url="https://air-inside.nvidia.com",
            env_file=env_internal,
            bcm_version="10.30.0",
        ),
        TestCase(
            key="test6",
            name="BCM 11.0 on air-inside.nvidia.com (.env.internal)",
            api_url="https://air-inside.nvidia.com",
            env_file=env_internal,
            bcm_version="11",  # Matches bcm-11.0-ubuntu2404.iso
        ),
    ]

    tests = _select_tests(all_tests, args)
    _ensure_log_dir()

    # Preflight check: warn about ISO availability
    iso_dir = REPO_ROOT / ".iso"
    if iso_dir.exists():
        iso_names = [f.name.lower() for f in iso_dir.glob("*.iso")]
        print(f"\nPreflight: found {len(iso_names)} ISO(s) in .iso/")
        for t in tests:
            major = t.bcm_version.split(".")[0]
            # Prefer matching the requested version string if it includes a dot (e.g., 10.30.0)
            needle = t.bcm_version.lower()
            matching: List[str]
            if "." in needle:
                matching = [n for n in iso_names if needle.replace(".", "") in n.replace(".", "") or needle in n]
            else:
                matching = [n for n in iso_names if f"bcm-{major}" in n or f"bcm{major}" in n]
            if not matching:
                print(f"  ⚠ WARNING: {t.key} requests BCM {t.bcm_version} but no BCM {major} ISO found!")
            else:
                print(f"  ✓ {t.key}: BCM {t.bcm_version} → likely matches {matching[0]}")
    else:
        print(f"\n⚠ WARNING: .iso/ directory not found - all tests will fail!")

    loop_start_time = time.time()
    _append_line(SUMMARY_LOG, f"[{_now()}] Test loop starting: {', '.join(t.key for t in tests)} (dry_run={args.dry_run})")

    overall_failures = 0
    test_timings: List[Tuple[str, float]] = []  # (test_key, elapsed_seconds)

    for test in tests:
        extra_env = _parse_dotenv(test.env_file)

        # Clear stale progress.json to avoid confusion if this test fails early
        if PROGRESS_JSON.exists() and not args.dry_run:
            PROGRESS_JSON.unlink()

        # Run the deployment with timing
        test_start_time = time.time()
        rc, parsed_sim_id, parsed_sim_name = _run_deploy(test, extra_env=extra_env, dry_run=args.dry_run)
        test_elapsed = time.time() - test_start_time
        test_timings.append((test.key, test_elapsed))

        ok = (rc == 0)
        status = "SUCCESS" if ok else f"FAIL(rc={rc})"
        sim_id, sim_name = parsed_sim_id, parsed_sim_name
        if not sim_id or not sim_name:
            file_sim_id, file_sim_name = _read_progress_sim_id(PROGRESS_JSON)
            sim_id = sim_id or file_sim_id
            sim_name = sim_name or file_sim_name
        _append_line(
            SUMMARY_LOG,
            f"[{_now()}] {test.key} {status} | elapsed={_format_elapsed(test_elapsed)} | bcm={test.bcm_version} | sim_name={sim_name or 'n/a'}",
        )

        if not args.dry_run:
            # Always attempt cleanup after a run (success or failure).
            if sim_id:
                try:
                    username = extra_env.get("AIR_USERNAME") or os.getenv("AIR_USERNAME") or ""
                    api_token = extra_env.get("AIR_API_TOKEN") or os.getenv("AIR_API_TOKEN") or ""
                    if not username or not api_token:
                        raise RuntimeError("Missing AIR_USERNAME or AIR_API_TOKEN in env; cannot delete simulation")
                    jwt = _air_login_jwt(test.api_url, username=username, api_token=api_token)
                    deleted, msg = _air_delete_simulation(test.api_url, jwt_token=jwt, simulation_id=sim_id)
                    _append_line(SUMMARY_LOG, f"[{_now()}] {test.key} cleanup sim_id={sim_id}: {msg}")
                    if not deleted:
                        _append_line(SUMMARY_LOG, f"[{_now()}] {test.key} WARNING: cleanup failed; you may need to delete manually")
                except Exception as e:
                    _append_line(SUMMARY_LOG, f"[{_now()}] {test.key} WARNING: cleanup exception: {e}")
            else:
                _append_line(SUMMARY_LOG, f"[{_now()}] {test.key} cleanup skipped (no simulation_id in {PROGRESS_JSON})")

        if not ok:
            overall_failures += 1
            if args.stop_on_fail:
                _append_line(SUMMARY_LOG, f"[{_now()}] stop-on-fail enabled; exiting early after {test.key}")
                break

        # Brief pause between tests to let APIs settle
        if test != tests[-1] and not args.dry_run:
            time.sleep(10)

    # Final summary with timing
    loop_elapsed = time.time() - loop_start_time
    _append_line(SUMMARY_LOG, f"[{_now()}] Test loop finished. failures={overall_failures}/{len(tests)} | total_elapsed={_format_elapsed(loop_elapsed)}")
    
    # Timing breakdown
    _append_line(SUMMARY_LOG, f"[{_now()}] Timing breakdown:")
    for test_key, elapsed in test_timings:
        _append_line(SUMMARY_LOG, f"           {test_key}: {_format_elapsed(elapsed)}")
    return 0 if overall_failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())


