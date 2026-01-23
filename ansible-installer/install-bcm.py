#!/usr/bin/env python3
"""
Local BCM installer wrapper.

Intended usage:
  - Clone this repo on the *target head node* (e.g., in your private OpenStack env)
  - Run this script locally to prepare and execute scripts/bcm_install.sh

This script:
  - Parses BCM version from the ISO filename (unless overridden)
  - Pins the correct brightcomputing.installer110 Ansible Galaxy collection for BCM 11.x
  - Ensures the ISO is available at /home/ubuntu/bcm.iso (symlink by default)
  - Templates scripts/bcm_install.sh placeholders and runs it
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
import json
import pwd
from pathlib import Path
from typing import Optional

from urllib.parse import urlencode
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]


def _parse_bcm_version_from_iso_name(iso_path: str) -> str:
    """
    Extract a BCM version from an ISO filename/path.

    Supports common formats:
      - 10.25.03
      - 11.30.0
      - 11.31.0
    """
    s = str(iso_path)
    # Match 10.x.y or 11.x.y where x/y are 1-2 digits (BCM10 uses e.g. 25.03, BCM11 uses e.g. 30.0).
    m = re.search(r"\b(10|11)\.(\d{1,2})\.(\d{1,2})\b", s)
    if not m:
        raise ValueError(f"Could not parse BCM version from ISO path/name: {iso_path}")
    major_s, minor_s, patch_s = m.group(1), m.group(2), m.group(3)
    major = int(major_s)
    minor = int(minor_s)
    patch = int(patch_s)
    if major == 10:
        # BCM10 commonly uses two-digit patch (e.g. 10.25.03)
        return f"{major}.{minor:02d}.{patch:02d}"
    return f"{major}.{minor:02d}.{patch}"


def _http_get_json(url: str, params: dict[str, object] | None = None, timeout_s: int = 20) -> object:
    q = urlencode({k: str(v) for k, v in (params or {}).items()})
    full = f"{url}?{q}" if q else url
    req = Request(full, headers={"Accept": "application/json"})
    with urlopen(req, timeout=timeout_s) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body)


def _select_installer110_collection_version(bcm_version: str) -> Optional[str]:
    """
    Pick an installer110 collection version aligned with BCM 11.<minor>.x ISO.

    Policy:
      - If BCM_INSTALLER110_VERSION is set, use it verbatim (escape hatch).
      - Else, query Ansible Galaxy and pick the latest version whose prefix matches:
          <bcm_minor>.0.*
        Examples:
          BCM 11.30.0 -> installer110 30.0.433+git...
          BCM 11.31.0 -> installer110 31.0.448+git...
    """
    override = (os.getenv("BCM_INSTALLER110_VERSION") or "").strip()
    if override:
        return override

    if not bcm_version or not str(bcm_version).startswith("11."):
        return None

    parts = str(bcm_version).split(".")
    if len(parts) < 2:
        return None
    bcm_minor = parts[1].strip()
    if not bcm_minor.isdigit():
        return None

    want_prefix = f"{int(bcm_minor)}.0."

    url = "https://galaxy.ansible.com/api/v3/plugin/ansible/content/published/collections/index/brightcomputing/installer110/versions/"
    payload = _http_get_json(url, params={"page_size": 200}, timeout_s=20)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return None
    versions = [str(x.get("version")) for x in data if isinstance(x, dict) and x.get("version")]

    cand = [v for v in versions if v.startswith(want_prefix)]
    if not cand:
        return None

    def _key(v: str) -> tuple[int, int, int]:
        base = v.split("+", 1)[0]
        ps = base.split(".")
        try:
            a = int(ps[0]) if len(ps) > 0 else 0
            b = int(ps[1]) if len(ps) > 1 else 0
            c = int(ps[2]) if len(ps) > 2 else 0
        except Exception:
            return (0, 0, 0)
        return (a, b, c)

    return sorted(cand, key=_key)[-1]


def _ensure_iso_at(iso_src: Path, iso_dest: Path, mode: str, force: bool) -> None:
    if not iso_src.exists():
        raise FileNotFoundError(f"ISO not found: {iso_src}")
    iso_dest.parent.mkdir(parents=True, exist_ok=True)

    if iso_dest.exists() or iso_dest.is_symlink():
        # If already points to the right place, do nothing.
        try:
            if iso_dest.is_symlink() and iso_dest.resolve() == iso_src.resolve():
                return
            if iso_dest.exists() and iso_dest.resolve() == iso_src.resolve():
                return
        except Exception:
            pass

        if not force:
            raise FileExistsError(
                f"Destination already exists: {iso_dest}\n"
                f"Refusing to overwrite without --force.\n"
                f"If this is safe, re-run with --force."
            )
        # Explicit overwrite requested.
        try:
            iso_dest.unlink()
        except FileNotFoundError:
            pass

    if mode == "symlink":
        iso_dest.symlink_to(iso_src)
    elif mode == "copy":
        shutil.copy2(iso_src, iso_dest)
    else:
        raise ValueError(f"Unknown mode: {mode}")


def _stage_collection_patch(bcm_version_full: str) -> Optional[Path]:
    """
    If a per-version collection patch exists in this repo (scripts/patches/<ver>.py),
    copy it to /home/ubuntu/bcm_patches/<ver>.py where scripts/bcm_install.sh expects it.

    Returns the staged patch path if staged, else None.
    """
    if os.getenv("BCM_SKIP_COLLECTION_PATCH", "").strip().lower() in ("1", "true", "yes"):
        return None

    src = REPO_ROOT / "scripts" / "patches" / f"{bcm_version_full}.py"
    if not src.exists():
        return None

    dst_dir = Path(os.environ.get("BCM_PATCH_DIR") or "/home/ubuntu/bcm_patches")
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    shutil.copy2(src, dst)
    return dst


def _render_bcm_install_sh(
    *,
    bcm_version_full: str,
    password: str,
    product_key: str,
    admin_email: str,
    external_interface: str,
    management_interface: str,
    internalnet_ip: str,
    internalnet_base: str,
    internalnet_prefixlen: str,
    installer110_pin: Optional[str],
) -> str:
    tmpl = REPO_ROOT / "scripts" / "bcm_install.sh"
    if not tmpl.exists():
        raise FileNotFoundError(f"Missing template script: {tmpl}")
    s = tmpl.read_text(encoding="utf-8")

    major = str(bcm_version_full).split(".", 1)[0].strip()

    s = s.replace("__PASSWORD__", password)
    s = s.replace("__PRODUCT_KEY__", product_key)
    s = s.replace("__BCM_VERSION__", major)
    s = s.replace("__BCM_FULL_VERSION__", bcm_version_full)
    s = s.replace("__ADMIN_EMAIL__", admin_email)
    s = s.replace("__EXTERNAL_INTERFACE__", external_interface)
    s = s.replace("__MANAGEMENT_INTERFACE__", management_interface)
    s = s.replace("__INTERNALNET_IP__", internalnet_ip)
    s = s.replace("__INTERNALNET_BASE__", internalnet_base)
    s = s.replace("__INTERNALNET_PREFIXLEN__", internalnet_prefixlen)

    pin = installer110_pin
    if str(bcm_version_full).startswith("11.") and not pin:
        # Match deploy behavior: fail fast on BCM 11.x if we can't deterministically pin.
        pin = "__AUTO_PIN_REQUIRED__"
    s = s.replace("__BCM_COLLECTION_VERSION__", pin or "")

    return s


def main() -> int:
    p = argparse.ArgumentParser(description="Install BCM locally using the Bright Ansible installer playbook.")
    p.add_argument("--iso", required=True, help="Path to BCM ISO (e.g. ./BCM-11.30.0.iso)")
    p.add_argument("--bcm-version", default="", help="Override BCM version (e.g. 11.30.0). Default: parse from ISO name")

    p.add_argument("--product-key", required=True, help="BCM product key / license")
    p.add_argument("--password", required=True, help="Default password to configure during install")
    p.add_argument("--admin-email", default="admin@example.com", help="Admin email to set in BCM")

    p.add_argument("--external-interface", default="ens3", help="Outbound interface (DHCP) on the head node")
    p.add_argument("--management-interface", default="ens4", help="Internal cluster network interface (internalnet)")
    p.add_argument(
        "--single-nic",
        action="store_true",
        help="Single-NIC mode: do NOT create BCM externalnet. Uses only --management-interface for internalnet; leaves external_interface empty.",
    )
    p.add_argument("--internalnet-ip", default="", help="internalnet IP for bond0 (optional; used by our template)")
    p.add_argument("--internalnet-base", default="", help="internalnet base (optional; used by our template)")
    p.add_argument("--internalnet-prefixlen", default="", help="internalnet prefixlen (optional; used by our template)")

    p.add_argument(
        "--run-user",
        default="",
        help="Non-root user whose home directory will store ISO/logs (default: SUDO_USER, else ubuntu, else root).",
    )
    p.add_argument("--iso-dest", default="", help="Where bcm_install.sh expects the ISO (default: <run-user-home>/bcm.iso)")
    p.add_argument("--iso-mode", choices=["symlink", "copy"], default="symlink", help="How to place ISO at --iso-dest")
    p.add_argument("--force", action="store_true", help="Overwrite --iso-dest if it already exists")

    p.add_argument("--script-out", default="/tmp/bcm_install.sh", help="Where to write the rendered install script")
    p.add_argument("--dry-run", action="store_true", help="Render/prepare but do not execute install script")

    args = p.parse_args()

    if os.geteuid() != 0:
        print("✗ This script must be run as root (or via sudo).", file=sys.stderr)
        print(f"  Try: sudo -E {sys.executable} {Path(__file__).resolve()} {' '.join(sys.argv[1:])}", file=sys.stderr)
        return 2

    # If invoked with sudo -E, HOME can remain set to the invoking user, which can confuse
    # Ansible roles that derive paths from ansible_env.HOME while other helper scripts assume
    # root's home is /root. Keep it sane.
    if os.geteuid() == 0:
        home = (os.environ.get("HOME") or "").strip()
        if home and home != "/root":
            print(f"⚠ Note: HOME={home} while running as root. Consider using `sudo -H -E ...`.")

    iso_src = Path(args.iso).expanduser().resolve()
    bcm_version = (args.bcm_version or "").strip()
    if not bcm_version:
        bcm_version = _parse_bcm_version_from_iso_name(str(iso_src))

    if args.single_nic:
        # For non-cloud installs, the Bright installer treats externalnet as optional:
        # externalnetwork is only active when external_interface is non-empty.
        #
        # This avoids forcing external_interface == management_interface (which can be rejected
        # by assumptions in some environments) while still allowing outbound access via the
        # management interface if the underlying network provides it.
        args.external_interface = ""

    installer110_pin: Optional[str] = None
    if bcm_version.startswith("11."):
        try:
            installer110_pin = _select_installer110_collection_version(bcm_version)
        except Exception as e:
            print(f"⚠ Could not query Ansible Galaxy for installer110 pin: {e}", file=sys.stderr)
            installer110_pin = None

    print(f"BCM version: {bcm_version}")
    if bcm_version.startswith("11."):
        print(f"installer110 pin: {installer110_pin or '(required but not determined)'}")
    if args.single_nic:
        print(f"ℹ single-NIC mode: externalnet will NOT be created; internalnet uses {args.management_interface}")

    # Determine a "run user" for where we stage ISO/patches/logs. The install itself runs as root
    # (via sudo), but many of our scripts historically used /home/ubuntu paths.
    run_user = (args.run_user or "").strip()
    if not run_user:
        run_user = (os.environ.get("SUDO_USER") or "").strip() or "ubuntu"
    try:
        run_home = Path(pwd.getpwnam(run_user).pw_dir).resolve()
    except Exception:
        # Fallback to root if ubuntu (or provided user) doesn't exist.
        run_user = "root"
        run_home = Path(pwd.getpwnam(run_user).pw_dir).resolve()

    iso_dest = Path((args.iso_dest or "").strip() or str(run_home / "bcm.iso"))
    patch_dir = Path(str(run_home / "bcm_patches"))

    # Pass these through to bcm_install.sh and our patch staging helper.
    os.environ["BCM_RUN_USER"] = run_user
    os.environ["BCM_USER_HOME"] = str(run_home)
    os.environ["BCM_ISO_PATH"] = str(iso_dest)
    os.environ["BCM_PATCH_DIR"] = str(patch_dir)
    os.environ.setdefault("ANSIBLE_LOG_PATH", str(run_home / "ansible_bcm_install.log"))

    print(f"Run user: {run_user} (home={run_home})")

    # If there is a known per-version collection patch (e.g. BCM 11.31.0 Slurm selection fix),
    # stage it where bcm_install.sh expects it.
    staged_patch = _stage_collection_patch(bcm_version)
    if staged_patch:
        print(f"Staged collection patch: {staged_patch}")

    _ensure_iso_at(iso_src, iso_dest, mode=args.iso_mode, force=args.force)
    print(f"ISO ready at: {iso_dest}")

    rendered = _render_bcm_install_sh(
        bcm_version_full=bcm_version,
        password=args.password,
        product_key=args.product_key,
        admin_email=args.admin_email,
        external_interface=args.external_interface,
        management_interface=args.management_interface,
        internalnet_ip=args.internalnet_ip,
        internalnet_base=args.internalnet_base,
        internalnet_prefixlen=args.internalnet_prefixlen,
        installer110_pin=installer110_pin,
    )

    out = Path(args.script_out)
    out.write_text(rendered, encoding="utf-8")
    out.chmod(out.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"Rendered install script: {out}")

    if args.dry_run:
        print("Dry run complete (not executing).")
        return 0

    print("\nRunning installer... (this can take a while)")
    proc = subprocess.run(["/usr/bin/env", "bash", str(out)], text=True, env=os.environ.copy())
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())


