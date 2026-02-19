#!/usr/bin/env python3
"""
Run BCM post-install actions locally on the head node.

This is the "no Air API" companion to deploy_bcm_air.py's features.yaml support:
  - Executes cmsh-based configuration scripts from your topology directory
  - Performs local-only gates like "wait_for_switches_up" via cmsh polling
  - Skips any actions that normally require NVIDIA Air API access (e.g., node resets)
    and prints a manual checklist so users can do those steps themselves.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


CMSH_BIN = "/cm/local/apps/cmd/bin/cmsh"
SWITCH_HTTP_ROOT = Path("/cm/local/apps/cmd/etc/htdocs/switch")


def _require_root() -> None:
    if os.geteuid() != 0:
        print("✗ This script must be run as root (or via sudo).", file=sys.stderr)
        print(f"  Try: sudo -H -E {sys.executable} {Path(__file__).resolve()} {' '.join(sys.argv[1:])}", file=sys.stderr)
        raise SystemExit(2)


def _detect_bcm_major(explicit_version: str) -> str:
    s = (explicit_version or "").strip()
    if s:
        m = re.match(r"^(10|11)\b", s)
        if m:
            return m.group(1)

    # Best-effort local detection from common release files.
    for p in ("/etc/cm-release", "/etc/cm-install-release"):
        try:
            txt = Path(p).read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        m = re.search(r"\b(10|11)\.(\d{1,2})\.(\d{1,2})\b", txt)
        if m:
            return m.group(1)

    raise ValueError(
        "Could not detect BCM major version (10/11). "
        "Please pass --bcm-version (e.g. 10.30.0 or 11.31.0)."
    )


def _resolve_versioned_value(value: Any, bcm_major: str) -> Any:
    if isinstance(value, dict):
        # Keys are expected to be "10" / "11"
        if bcm_major in value:
            return value[bcm_major]
        # Fall back to "best effort": pick highest numeric key if present
        try:
            keys = sorted([int(k) for k in value.keys() if str(k).isdigit()])
            if keys:
                return value[str(keys[-1])]
        except Exception:
            pass
    return value


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    # Ensure BCM certs/paths that assume root HOME work reliably.
    env.setdefault("HOME", "/root")
    return subprocess.run(cmd, check=check, text=True, capture_output=False, env=env)


def _run_cmsh_line(line: str) -> None:
    s = (line or "").strip()
    if not s or s.startswith("#"):
        return

    if s.startswith("cmsh "):
        args = shlex.split(s)
        args[0] = CMSH_BIN
        _run(args)
        return

    if s.startswith(CMSH_BIN + " "):
        args = shlex.split(s)
        _run(args)
        return

    # Treat as a raw cmsh command (e.g. "device; list")
    _run([CMSH_BIN, "-c", s])


def _run_cmsh_script(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"cmsh script not found: {path}")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for ln in lines:
        _run_cmsh_line(ln)


def _sync_switch_configs_dir(local_dir: Path) -> None:
    if not local_dir.exists() or not local_dir.is_dir():
        raise FileNotFoundError(f"switch_configs_dir not found (or not a directory): {local_dir}")

    SWITCH_HTTP_ROOT.mkdir(parents=True, exist_ok=True)

    src = str(local_dir) + "/"
    dst = str(SWITCH_HTTP_ROOT) + "/"
    if shutil.which("rsync"):
        _run(["rsync", "-a", src, dst])
    else:
        # Minimal fallback: recursively copy directory structure.
        for child in local_dir.iterdir():
            target = SWITCH_HTTP_ROOT / child.name
            if child.is_dir():
                shutil.copytree(child, target, dirs_exist_ok=True)
            else:
                shutil.copy2(child, target)

    # Ensure readable by HTTP server
    _run(["bash", "-lc", f"find {shlex.quote(str(SWITCH_HTTP_ROOT))} -type d -exec chmod 755 {{}} \\;"])
    _run(["bash", "-lc", f"find {shlex.quote(str(SWITCH_HTTP_ROOT))} -type f -exec chmod 644 {{}} \\;"])


def _wait_for_switches_up(switch_names: list[str], *, timeout: int = 1800, interval: int = 20) -> None:
    wanted = [str(s).strip() for s in (switch_names or []) if str(s).strip()]
    wanted_lower = {s.lower() for s in wanted}
    if not wanted_lower:
        return

    print(f"Waiting for switches UP: {', '.join(wanted)} (timeout={timeout}s, interval={interval}s)")
    start = time.time()
    last_msg = 0.0
    last_missing: set[str] | None = None

    while time.time() - start < timeout:
        r = subprocess.run(
            [CMSH_BIN, "-c", "device;list -t switch"],
            text=True,
            capture_output=True,
            env={**os.environ.copy(), "HOME": "/root"},
        )
        statuses: dict[str, str] = {}
        for line in (r.stdout or "").splitlines():
            s = line.rstrip("\n")
            if not s.strip():
                continue
            if s.startswith("Type") or s.startswith("---"):
                continue
            if not s.strip().startswith("Switch"):
                continue
            m = re.search(r"\[\s*([^\]]+?)\s*\]", s)
            if not m:
                continue
            st = m.group(1).strip()
            parts = s.split()
            if len(parts) < 2:
                continue
            host = parts[1].strip().lower()
            if host:
                statuses[host] = st

        up_now = {h for h, st in statuses.items() if st == "UP"}
        missing = set(wanted_lower - up_now)
        if not missing:
            print("✓ All specified switches are UP")
            return

        if last_missing is None or missing != last_missing or (time.time() - last_msg) > 30:
            print(f"(still waiting; missing: {', '.join(sorted(missing))})")
            last_missing = set(missing)
            last_msg = time.time()

        time.sleep(max(1, interval))

    raise TimeoutError(f"Timed out waiting for switches UP (timeout={timeout}s)")


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if value is True:
        return ["<auto-detect (not supported without Air API)>"]
    return []


def main() -> int:
    p = argparse.ArgumentParser(description="Run BCM topology post-install actions locally (no NVIDIA Air API).")
    p.add_argument("--topology", required=True, help="Topology directory containing features.yaml (e.g. topologies/preconfigured)")
    p.add_argument(
        "--bcm-version",
        default="",
        help="BCM version (e.g. 10.30.0 or 11.31.0). Used to select versioned config_file entries.",
    )
    p.add_argument("--dry-run", action="store_true", help="Print what would run, but do not execute changes")

    args = p.parse_args()
    _require_root()

    if yaml is None:
        print("✗ PyYAML is required (pip install pyyaml).", file=sys.stderr)
        return 2

    topology_dir = Path(args.topology).expanduser().resolve()
    features_path = topology_dir / "features.yaml"
    if not features_path.exists():
        print(f"✗ features.yaml not found: {features_path}", file=sys.stderr)
        return 2

    bcm_major = _detect_bcm_major(args.bcm_version)
    features = yaml.safe_load(features_path.read_text(encoding="utf-8")) or {}
    if not isinstance(features, dict):
        print(f"✗ Unexpected features.yaml structure (expected mapping): {features_path}", file=sys.stderr)
        return 2

    manual_steps: list[str] = []
    print(f"Topology: {topology_dir}")
    print(f"BCM major: {bcm_major}")

    for feature_name, config in features.items():
        if not isinstance(config, dict):
            continue
        if not bool(config.get("enabled", False)):
            print(f"- {feature_name}: disabled (skipping)")
            continue

        print(f"- {feature_name}: running")

        # API-only knobs we intentionally skip in local mode (but we surface as a checklist).
        if feature_name == "bcm_switches":
            switches = _as_list(config.get("reboot_switches_after", False))
            if switches:
                manual_steps.append(f"Power-cycle/reset switches after configuration: {', '.join(switches)}")

        if feature_name == "bcm_nodes":
            nodes = _as_list(config.get("reset_nodes_after", False))
            if nodes:
                manual_steps.append(f"Reset compute nodes after switches are UP: {', '.join(nodes)}")
            if bool(config.get("installer_failed_monitoring", False)):
                manual_steps.append(
                    "If newly-reset nodes enter status INSTALLER_FAILED, reset them again (two-phase reset workflow)."
                )

        if feature_name == "bcm_networking" and bool(config.get("reboot_after", False)):
            manual_steps.append("Reboot the BCM head node if required after networking changes.")

        # Local switch config sync (no SSH needed).
        if feature_name == "bcm_switches":
            scd = _resolve_versioned_value(config.get("switch_configs_dir"), bcm_major)
            if isinstance(scd, str) and scd.strip():
                local_dir = topology_dir / scd
                print(f"  - syncing switch configs: {local_dir} -> {SWITCH_HTTP_ROOT}")
                if not args.dry_run:
                    _sync_switch_configs_dir(local_dir)

        # Run cmsh script (config_file).
        cfg = _resolve_versioned_value(config.get("config_file"), bcm_major)
        if isinstance(cfg, str) and cfg.strip():
            script_path = topology_dir / cfg
            print(f"  - cmsh script: {script_path}")
            if not args.dry_run:
                _run_cmsh_script(script_path)
        else:
            print("  - no config_file specified (skipping cmsh script)")

        # Local dependency gates.
        if feature_name == "bcm_nodes":
            wait_names = config.get("wait_for_switches_up") or []
            timeout = int(config.get("wait_for_switches_timeout", 1800) or 1800)
            interval = int(config.get("wait_for_switches_interval", 20) or 20)
            if isinstance(wait_names, list) and wait_names:
                if args.dry_run:
                    print(f"  - would wait for switches UP: {', '.join([str(x) for x in wait_names])}")
                else:
                    _wait_for_switches_up([str(x) for x in wait_names], timeout=timeout, interval=interval)

    print("\nManual steps (skipped because they normally require Air API control):")
    if manual_steps:
        for s in manual_steps:
            print(f"- {s}")
    else:
        print("- (none)")

    print("\nIn a fully automated NVIDIA Air deployment, we would also typically:")
    print("- Create/manage the simulation lifecycle (create, delete, resume)")
    print("- Reset/power-cycle switches and PXE nodes via the Air control plane")
    print("- Monitor node provisioning states centrally and trigger retries")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

