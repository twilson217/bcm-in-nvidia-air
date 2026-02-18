"""
Topology-specific post-deploy tests for topologies/preconfigured.

This module is loaded dynamically by scripts/test-loop.py when running:
  python scripts/test-loop.py --topology topologies/preconfigured

Contract:
  - Export run_tests(context: dict) -> bool | list[dict] | list[tuple]
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


CMSH_BIN = "/cm/local/apps/cmd/bin/cmsh"


@dataclass(frozen=True)
class Result:
    name: str
    ok: bool
    details: str = ""


def _run_ssh(ssh_config_file: str, cmd: str, timeout: int = 60) -> Tuple[int, str, str]:
    """
    Execute a remote command on the BCM head node via the generated SSH config.

    We use the 'bcm' alias from the SSH config (created by deploy_bcm_air.py).
    """
    p = subprocess.run(
        [
            "ssh",
            "-F",
            ssh_config_file,
            "bcm",
            # Use bash so we can use pipes/grep robustly.
            f"bash -lc {cmd!r}",
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return p.returncode, p.stdout or "", p.stderr or ""


def _run_cmsh(ssh_config_file: str, cmsh_cmd: str, timeout: int = 60) -> Tuple[int, str, str]:
    """
    Run a single cmsh -c command as root (sudo -H) with full cmsh path.
    """
    return _run_ssh(ssh_config_file, f"sudo -H {CMSH_BIN} -c {cmsh_cmd!r}", timeout=timeout)


def _poll_until(
    *,
    desc: str,
    timeout_s: int,
    interval_s: int,
    fn: Callable[[], Tuple[bool, str]],
) -> Result:
    start = time.monotonic()
    deadline = time.monotonic() + timeout_s
    last_details = ""
    while True:
        ok, details = fn()
        last_details = details
        if ok:
            elapsed = int(time.monotonic() - start)
            suffix = f"(elapsed={elapsed}s, timeout={timeout_s}s, interval={interval_s}s)"
            joined = f"{suffix} | {details}" if details else suffix
            return Result(name=desc, ok=True, details=joined)
        if time.monotonic() >= deadline:
            elapsed = int(time.monotonic() - start)
            suffix = f"timeout after {timeout_s}s (elapsed={elapsed}s, interval={interval_s}s)"
            joined = f"{suffix} | {details}" if details else (last_details or suffix)
            return Result(name=desc, ok=False, details=joined)
        time.sleep(interval_s)


def _read_switch_names(topology_dir: Path) -> List[str]:
    """
    Switch names are represented by subdirectories in bcm-config/switch-configs/,
    but not all directories are actual switches (e.g. bootstrap, template).
    """
    root = topology_dir / "bcm-config" / "switch-configs"
    if not root.exists():
        return []
    names: List[str] = []
    for p in sorted(root.iterdir()):
        if not p.is_dir():
            continue
        if p.name in ("bootstrap", "template"):
            continue
        names.append(p.name)
    return names


def _read_compute_nodes(topology_dir: Path) -> List[Tuple[str, str, List[str]]]:
    """
    Parse bcm-config/nodes.cmsh to extract:
      - node name (cpu-01, ...)
      - node IP (10.1.37.xx)
      - macs mentioned in the line (for best-effort DHCP log checks)

    Returns list of (name, ip, macs).
    """
    path = topology_dir / "bcm-config" / "nodes.cmsh"
    if not path.exists():
        return []

    out: List[Tuple[str, str, List[str]]] = []
    text = path.read_text(encoding="utf-8", errors="replace")

    # Example snippet:
    #   cmsh -c "device;add physicalnode cpu-01 10.1.37.11 bond0; ... set mac 48:..; ... set mac 48:..; ..."
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.search(r"add\s+physicalnode\s+(\S+)\s+(\d{1,3}(?:\.\d{1,3}){3})\b", line, re.IGNORECASE)
        if not m:
            continue
        name = m.group(1)
        ip = m.group(2)
        macs = re.findall(r"\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b", line)
        out.append((name, ip, macs))
    return out


def run_tests(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    topology_dir = Path(context["topology_dir"])
    ssh_config = context.get("ssh_config_file")
    bcm_version = str(context.get("bcm_version") or "")

    results: List[Result] = []

    if not ssh_config:
        return [{"name": "topology-test preflight: ssh_config_file missing", "ok": False, "details": "test-loop must provide ssh_config_file via progress.json"}]
    if not Path(ssh_config).exists():
        return [{"name": "topology-test preflight: ssh_config_file not found", "ok": False, "details": ssh_config}]

    # ---------------------------------------------------------------------
    # BCM Networking (aligns to bcm-config/networking.cmsh)
    # ---------------------------------------------------------------------
    # 1) bond0 exists
    rc, out, err = _run_ssh(ssh_config, "ip link show bond0", timeout=30)
    results.append(Result("bcm_networking: bond0 present", ok=(rc == 0), details=(err.strip() or out.strip())[:400]))

    # 2) eth3 has 10.1.28.254
    def _eth3_ip_ok() -> Tuple[bool, str]:
        rc2, out2, err2 = _run_ssh(ssh_config, "ip -4 addr show dev eth3", timeout=30)
        if rc2 != 0:
            return False, (err2.strip() or out2.strip())[:400]
        return ("10.1.28.254/" in out2), out2.strip()[:400]

    results.append(_poll_until(desc="bcm_networking: eth3 has 10.1.28.254", timeout_s=120, interval_s=5, fn=_eth3_ip_ok))

    # 3) static route 10.0.0.0/15 via 192.168.200.1
    def _route_ok() -> Tuple[bool, str]:
        rc2, out2, err2 = _run_ssh(ssh_config, "ip route show 10.0.0.0/15", timeout=30)
        if rc2 != 0:
            return False, (err2.strip() or out2.strip())[:400]
        s = out2.strip()
        ok = ("via 192.168.200.1" in s) and ("10.0.0.0/15" in s)
        return ok, s[:400]

    results.append(_poll_until(desc="bcm_networking: static route 10.0.0.0/15 via 192.168.200.1", timeout_s=120, interval_s=5, fn=_route_ok))

    # ---------------------------------------------------------------------
    # BCM Switches (aligns to switches_v10.cmsh / switches_v11.cmsh + switch-configs)
    # ---------------------------------------------------------------------
    # Per request, timers start after BCM networking tests complete.
    switch_names = _read_switch_names(topology_dir)
    if not switch_names:
        results.append(Result("bcm_switches: switch-configs directories discovered", ok=False, details="No switch directories found under bcm-config/switch-configs/"))
    else:
        results.append(Result("bcm_switches: switch-configs directories discovered", ok=True, details=", ".join(switch_names)))

    # Timer #1 (10 min): 10.1.28.14 pingable
    def _ping(ip: str) -> Tuple[bool, str]:
        rc2, out2, err2 = _run_ssh(
            ssh_config,
            f"ping -c 1 -W 1 {ip} >/dev/null 2>&1 && echo OK || echo NO",
            timeout=15,
        )
        ok = (out2 or "").strip() == "OK"
        return ok, "ping OK" if ok else f"ping failed ({ip})"

    results.append(
        _poll_until(
            desc="bcm_switches timer#1: ping 10.1.28.14 (mgmt-leaf-02) from BCM",
            timeout_s=10 * 60,
            interval_s=10,
            fn=lambda: _ping("10.1.28.14"),
        )
    )

    # Timer #2 (10 min): mgmt-leaf-02 status UP
    def _mgmt_leaf_02_up() -> Tuple[bool, str]:
        rc2, out2, err2 = _run_cmsh(ssh_config, "device;use mgmt-leaf-02;status", timeout=30)
        txt = (out2 + "\n" + err2).strip()
        ok = bool(re.search(r"\bUP\b", txt))
        return ok, txt[:400]

    results.append(_poll_until(desc='bcm_switches timer#2: cmsh "device;use mgmt-leaf-02;status" contains UP', timeout_s=10 * 60, interval_s=15, fn=_mgmt_leaf_02_up))

    # Timer #3 (10 min): 192.168.200.254 pingable
    results.append(
        _poll_until(
            desc="bcm_switches timer#3: ping 192.168.200.254 (internalnet) from BCM",
            timeout_s=10 * 60,
            interval_s=10,
            fn=lambda: _ping("192.168.200.254"),
        )
    )

    # Timer #4 (10 min): all other switches UP
    other_switches = [s for s in switch_names if s != "mgmt-leaf-02"]

    def _all_other_switches_up() -> Tuple[bool, str]:
        bad: List[str] = []
        for s in other_switches:
            rc2, out2, err2 = _run_cmsh(ssh_config, f"device;use {s};status", timeout=30)
            txt = (out2 + "\n" + err2).strip()
            if not re.search(r"\bUP\b", txt):
                bad.append(s)
        if bad:
            return False, f"not UP: {', '.join(bad)}"
        return True, "all UP"

    results.append(_poll_until(desc="bcm_switches timer#4: all other switches show UP", timeout_s=10 * 60, interval_s=20, fn=_all_other_switches_up))

    # ---------------------------------------------------------------------
    # Compute Nodes (aligns to bcm-config/nodes.cmsh)
    # ---------------------------------------------------------------------
    compute_nodes = _read_compute_nodes(topology_dir)
    if not compute_nodes:
        results.append(Result("bcm_nodes: discovered compute nodes from nodes.cmsh", ok=False, details="No compute nodes parsed from bcm-config/nodes.cmsh"))
    else:
        details = ", ".join([f"{n}({ip})" for n, ip, _ in compute_nodes])
        results.append(Result("bcm_nodes: discovered compute nodes from nodes.cmsh", ok=True, details=details))

    # Timer #1 (10 min): best-effort check for DHCP activity (not a hard fail if we can't find it).
    # We try common dhcp service logs, but behavior can vary across BCM versions and OS images.
    def _dhcp_activity_seen() -> Tuple[bool, str]:
        macs = sorted({m.lower() for _, _, ms in compute_nodes for m in ms})
        if not macs:
            return True, "no MACs found in nodes.cmsh; skipping DHCP log check"

        # Try journalctl for common unit names; fall back to syslog grep.
        checks = [
            "sudo journalctl -u isc-dhcp-server -S -10min --no-pager 2>/dev/null || true",
            "sudo journalctl -u dhcpd -S -10min --no-pager 2>/dev/null || true",
            "sudo tail -n 2000 /var/log/syslog 2>/dev/null || true",
            "sudo tail -n 2000 /var/log/daemon.log 2>/dev/null || true",
        ]
        hay = ""
        for c in checks:
            rc2, out2, _ = _run_ssh(ssh_config, c, timeout=30)
            if rc2 == 0 and out2:
                hay += "\n" + out2

        if not hay.strip():
            return True, "WARN: could not read DHCP logs; will validate compute nodes via ping/status timers"

        hits = []
        for m in macs:
            if m in hay.lower():
                hits.append(m)
        if hits:
            return True, f"observed DHCP log activity for MAC(s): {', '.join(hits[:6])}"
        return True, "WARN: no DHCP log activity matched compute MACs; will validate via ping/status timers"

    results.append(_poll_until(desc="bcm_nodes timer#1: best-effort DHCP activity observed (non-fatal)", timeout_s=10 * 60, interval_s=20, fn=_dhcp_activity_seen))

    # Timer #2 (5 min): ping compute node IPs
    def _all_compute_pingable() -> Tuple[bool, str]:
        bad: List[str] = []
        for n, ip, _ in compute_nodes:
            rc2, out2, _ = _run_ssh(ssh_config, f"ping -c 1 -W 1 {ip} >/dev/null 2>&1 && echo OK || echo NO", timeout=15)
            if (out2 or "").strip() != "OK":
                bad.append(f"{n}({ip})")
        if bad:
            return False, f"unpingable: {', '.join(bad)}"
        return True, "all pingable"

    results.append(_poll_until(desc="bcm_nodes timer#2: ping all compute node IPs", timeout_s=5 * 60, interval_s=15, fn=_all_compute_pingable))

    # Timer #3 (10 min): status != DOWN
    def _all_compute_not_down() -> Tuple[bool, str]:
        still_down: List[str] = []
        for n, _, _ in compute_nodes:
            rc2, out2, err2 = _run_cmsh(ssh_config, f"device;use {n};status", timeout=30)
            txt = (out2 + "\n" + err2).strip().upper()
            if "DOWN" in txt and "UP" not in txt:
                still_down.append(n)
        if still_down:
            return False, f"still DOWN: {', '.join(still_down)}"
        return True, "none are DOWN-only"

    results.append(_poll_until(desc='bcm_nodes timer#3: cmsh status is not "DOWN" (for all compute nodes)', timeout_s=10 * 60, interval_s=20, fn=_all_compute_not_down))

    # Timer #4 (30 min): status == UP
    def _all_compute_up() -> Tuple[bool, str]:
        bad: List[str] = []
        snaps: List[str] = []
        for n, _, _ in compute_nodes:
            rc2, out2, err2 = _run_cmsh(ssh_config, f"device;use {n};status", timeout=30)
            txt = (out2 + "\n" + err2).strip()
            if not re.search(r"\bUP\b", txt):
                bad.append(n)
            # include a small, per-node snippet for debugging
            t = " ".join(txt.split())
            if not t:
                t = f"(no output, rc={rc2})"
            snaps.append(f"{n}={t[:120]}")
        if bad:
            return False, f"not UP: {', '.join(bad)} | " + "; ".join(snaps)
        return True, "all UP"

    results.append(_poll_until(desc='bcm_nodes timer#4: cmsh status contains "UP" (for all compute nodes)', timeout_s=30 * 60, interval_s=30, fn=_all_compute_up))

    # Convert to the structure expected by test-loop's plugin runner.
    return [{"name": r.name, "ok": r.ok, "details": r.details} for r in results]


