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

# rsync --info=progress2 emits frequent carriage-return progress updates that become
# newline-separated when captured via pipes. We want full progress on the console,
# but only a single (final) progress line in the file log.
_RSYNC_PROGRESS_RE = re.compile(
    r"^\s*\d[\d,]*\s+\d{1,3}%\s+[\d.]+\s*[kMG]B/s\s+\d+:\d{2}:\d{2}\s*$"
)


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


def _read_progress_ssh_config(progress_path: Path) -> Optional[str]:
    """
    Returns the ssh_config_file from .logs/progress.json if present.
    """
    if not progress_path.exists():
        return None
    try:
        import json

        data = json.loads(progress_path.read_text(encoding="utf-8"))
        return data.get("ssh_config_file")
    except Exception:
        return None


def _safe_slug(s: str) -> str:
    """Filesystem-safe-ish key for log filenames."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")


def _download_failure_logs(test_key: str, ssh_config: Optional[str], sim_name: Optional[str]) -> None:
    """
    Download logs from the failed simulation before it's deleted.
    Saves to .logs/ansible_bcm_install_{test_key}.log
    """
    if not ssh_config:
        _append_line(SUMMARY_LOG, f"[{_now()}] {test_key} WARNING: cannot download logs (no SSH config)")
        return
    
    ssh_config_path = Path(ssh_config)
    if not ssh_config_path.exists():
        _append_line(SUMMARY_LOG, f"[{_now()}] {test_key} WARNING: SSH config not found: {ssh_config}")
        return
    
    # Download ansible log
    remote_log = "/home/ubuntu/ansible_bcm_install.log"
    local_log = LOG_DIR / f"ansible_bcm_install_{_safe_slug(test_key)}.log"
    
    try:
        result = subprocess.run(
            ["scp", "-F", str(ssh_config_path), f"air-bcm-01:{remote_log}", str(local_log)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            _append_line(SUMMARY_LOG, f"[{_now()}] {test_key} downloaded ansible log to {local_log.name}")
            print(f"  ✓ Downloaded ansible log to {local_log}")
        else:
            _append_line(SUMMARY_LOG, f"[{_now()}] {test_key} ansible log download failed: {result.stderr.strip()[:100]}")
    except subprocess.TimeoutExpired:
        _append_line(SUMMARY_LOG, f"[{_now()}] {test_key} ansible log download timed out")
    except Exception as e:
        _append_line(SUMMARY_LOG, f"[{_now()}] {test_key} ansible log download error: {e}")
    
    # Also try to download the bcm_install.sh script output (if it exists)
    # The script itself might have failed before creating the ansible log
    try:
        # Check if bcm_install.sh exists and get any error output
        result = subprocess.run(
            ["ssh", "-F", str(ssh_config_path), "air-bcm-01", 
             "ls -la /home/ubuntu/bcm_install.sh 2>&1; ls -la /home/ubuntu/bcm-ansible-installer/ 2>&1 || echo 'No bcm-ansible-installer dir'"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            debug_log = LOG_DIR / f"bcm_debug_{_safe_slug(test_key)}.log"
            debug_log.write_text(f"Remote state check for {test_key}:\n{result.stdout}\n", encoding="utf-8")
            _append_line(SUMMARY_LOG, f"[{_now()}] {test_key} saved debug info to {debug_log.name}")
    except Exception:
        pass  # Best effort


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
        in_iso_rsync = False
        last_rsync_progress: Optional[str] = None

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
            # Always show full output on console
            sys.stdout.write(line)

            # Decide what to write to the file log.
            # While rsync is running, keep only the latest progress line in memory
            # and write it once when the upload completes.
            stripped = line.replace("\r", "").rstrip("\n")

            # Heuristic boundaries for ISO upload phase (rsync progress spam).
            if "📦 Uploading BCM ISO to head node" in stripped:
                in_iso_rsync = True
                last_rsync_progress = None

            if in_iso_rsync and _RSYNC_PROGRESS_RE.match(stripped):
                last_rsync_progress = stripped
                continue  # don't spam file log with progress lines

            # When ISO upload completes, flush the last rsync progress line once.
            if in_iso_rsync and ("✓ ISO uploaded successfully" in stripped or "✗ ISO upload failed" in stripped):
                if last_rsync_progress:
                    log_f.write(f"[rsync] {last_rsync_progress}\n")
                in_iso_rsync = False
                last_rsync_progress = None

            # Default: write line as-is
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


_ISO_VERSION_RE = re.compile(r"^bcm-(?P<version>\d+(?:\.\d+){0,2})-ubuntu2404\.iso$", re.IGNORECASE)


def _version_sort_key(v: str) -> Tuple[int, ...]:
    try:
        return tuple(int(p) for p in v.split("."))
    except Exception:
        # Put weird versions at the end deterministically
        return (9999, 9999, 9999)


def _discover_iso_versions(iso_dir: Path) -> List[str]:
    versions: List[str] = []
    if not iso_dir.exists():
        return versions
    for f in sorted(iso_dir.glob("*.iso")):
        m = _ISO_VERSION_RE.match(f.name)
        if not m:
            continue
        versions.append(m.group("version"))
    versions = sorted(set(versions), key=_version_sort_key)
    return versions


def _resolve_requested_versions(requested: List[str], available: List[str]) -> List[str]:
    """
    Resolve requested versions against available ISO versions.
    Supports:
      - exact version (e.g. 11.31.0)
      - major version (e.g. 11) only if it matches exactly one available version
    """
    if not requested:
        return available

    resolved: List[str] = []
    for r in requested:
        r = r.strip()
        if not r:
            continue
        if r in available:
            resolved.append(r)
            continue

        # Major version shorthand (e.g. "11")
        if "." not in r:
            matches = [v for v in available if v.split(".")[0] == r]
            if len(matches) == 1:
                resolved.append(matches[0])
                continue
            if len(matches) > 1:
                raise ValueError(f"Ambiguous --test {r}: matches {', '.join(matches)}; please specify full version")

        raise ValueError(f"Unknown --test {r}: no matching ISO in .iso/ (available: {', '.join(available) or 'none'})")

    # Preserve requested order, de-dup
    seen = set()
    out: List[str] = []
    for v in resolved:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an overnight deployment matrix test loop.")
    parser.add_argument(
        "--test",
        action="append",
        default=[],
        help=(
            "BCM version to test (repeatable). Examples: --test 10.30.0 --test 11.31.0. "
            "If omitted, all versions found in .iso/ are tested."
        ),
    )
    parser.add_argument(
        "--external",
        action="store_true",
        help="Run tests against air.nvidia.com only (unless --internal is also set).",
    )
    parser.add_argument(
        "--internal",
        action="store_true",
        help="Run tests against air-inside.nvidia.com only (unless --external is also set).",
    )

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

    iso_dir = REPO_ROOT / ".iso"
    available_versions = _discover_iso_versions(iso_dir)
    if not available_versions:
        print(f"✗ No ISO versions found in {iso_dir} (expected filenames like bcm-11.31.0-ubuntu2404.iso)", file=sys.stderr)
        return 2

    try:
        versions_to_test = _resolve_requested_versions(args.test, available_versions)
    except ValueError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2

    # Target selection: default to both; if user supplies only one flag, restrict.
    run_external = True
    run_internal = True
    if args.external and not args.internal:
        run_internal = False
    if args.internal and not args.external:
        run_external = False

    targets: List[Tuple[str, str, Path]] = []
    if run_external:
        targets.append(("external", "https://air.nvidia.com", env_external))
    if run_internal:
        targets.append(("internal", "https://air-inside.nvidia.com", env_internal))

    tests: List[TestCase] = []
    for v in versions_to_test:
        # Requirement: for each ISO/version, run air.nvidia.com first, then air-inside.
        for target_name, api_url, env_file in targets:
            key = f"{target_name}-{v}"
            tests.append(
                TestCase(
                    key=key,
                    name=f"BCM {v} on {api_url} ({env_file.name})",
                    api_url=api_url,
                    env_file=env_file,
                    bcm_version=v,
                )
            )

    _ensure_log_dir()

    # Preflight check: warn about ISO availability
    iso_names = [f.name for f in sorted(iso_dir.glob("*.iso"))]
    print(f"\nPreflight: found {len(iso_names)} ISO(s) in .iso/")
    for n in iso_names:
        print(f"  - {n}")
    print(f"\nPreflight: will run {len(tests)} test(s):")
    for t in tests:
        print(f"  - {t.key}: {t.name}")

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
            # If test failed, download logs BEFORE any cleanup.
            if not ok:
                ssh_config = _read_progress_ssh_config(PROGRESS_JSON)
                _download_failure_logs(test.key, ssh_config, sim_name)

            # If stop-on-fail is enabled, keep the failed simulation for investigation.
            if (not ok) and args.stop_on_fail:
                if sim_id:
                    _append_line(
                        SUMMARY_LOG,
                        f"[{_now()}] {test.key} cleanup skipped (stop-on-fail enabled; keeping sim_id={sim_id})",
                    )
                else:
                    _append_line(
                        SUMMARY_LOG,
                        f"[{_now()}] {test.key} cleanup skipped (stop-on-fail enabled; no simulation_id in {PROGRESS_JSON})",
                    )
            else:
                # Default: cleanup after a run (success or failure).
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


