#!/usr/bin/env python3

import argparse
import base64
import shlex
import subprocess
import sys
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple


SSH_PUBKEY_PATH = "/root/.ssh/id_ecdsa.pub"


@dataclass(frozen=True)
class Result:
    switch: str
    ok: bool
    stdout: str
    stderr: str
    returncode: int


def _run(
    argv: List[str],
    *,
    input_text: Optional[str] = None,
    timeout_s: int = 300,
) -> Tuple[int, str, str]:
    p = subprocess.run(
        argv,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout_s,
    )
    return p.returncode, p.stdout, p.stderr


def _parse_switches_csv(value: str) -> List[str]:
    switches: List[str] = []
    for raw in value.split(","):
        s = raw.strip()
        if s:
            switches.append(s)
    # de-dupe while preserving order
    seen = set()
    out: List[str] = []
    for s in switches:
        if s not in seen:
            out.append(s)
            seen.add(s)
    return out


def _discover_up_switches_from_cmsh() -> List[str]:
    rc, out, err = _run(["cmsh", "-c", 'device;list -t switch --status up'])
    if rc != 0:
        raise RuntimeError(f"cmsh failed (rc={rc}): {err.strip() or out.strip()}")

    switches: List[str] = []
    for line in out.splitlines():
        parts = line.split()
        # Expected rows look like:
        # Switch  leaf-01  48:B0:...  10.1.28.11  ... [UP]
        if len(parts) >= 2 and parts[0] == "Switch":
            switches.append(parts[1])

    # de-dupe while preserving order
    seen = set()
    out_sw: List[str] = []
    for s in switches:
        if s not in seen:
            out_sw.append(s)
            seen.add(s)
    return out_sw


def _read_pubkey() -> str:
    try:
        with open(SSH_PUBKEY_PATH, "r", encoding="utf-8") as f:
            key = f.read().strip()
    except FileNotFoundError:
        raise RuntimeError(f"Missing SSH public key: {SSH_PUBKEY_PATH}")
    if not key.startswith("ecdsa-"):
        # Not strictly required, but helps catch wrong file early.
        raise RuntimeError(
            f"Unexpected key format in {SSH_PUBKEY_PATH}. Expected an ecdsa public key line."
        )
    return key


def _remote_root_setup_script(pubkey: str, sudopass: str) -> str:
    pubkey_b64 = base64.b64encode(pubkey.encode("utf-8")).decode("ascii")
    sudopass_b64 = base64.b64encode(sudopass.encode("utf-8")).decode("ascii")

    # Note: this script runs on the switch.
    return f"""#!/usr/bin/env bash
set -euo pipefail

PUBKEY_B64={shlex.quote(pubkey_b64)}
SUDOPASS_B64={shlex.quote(sudopass_b64)}

pubkey="$(printf '%s' "$PUBKEY_B64" | base64 -d)"
sudopass="$(printf '%s' "$SUDOPASS_B64" | base64 -d)"

run_as_root() {{
  if [ "$(id -u)" -eq 0 ]; then
    bash -s
  elif sudo -n true 2>/dev/null; then
    sudo bash -s
  else
    # feed sudo password + script to sudo -S
    {{ printf '%s\\n' "$sudopass"; cat; }} | sudo -S -p '' bash -s
  fi
}}

run_as_root <<'ROOTSCRIPT'
set -euo pipefail

PUBKEY_B64={shlex.quote(pubkey_b64)}
pubkey="$(printf '%s' "$PUBKEY_B64" | base64 -d)"

install -d -m 700 /root/.ssh
touch /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys

grep -qxF "$pubkey" /root/.ssh/authorized_keys 2>/dev/null || printf '%s\\n' "$pubkey" >> /root/.ssh/authorized_keys

mkdir -p /etc/ssh/sshd_config.d
tee /etc/ssh/sshd_config.d/10-pubkey.conf >/dev/null <<'EOF'
PubkeyAuthentication yes
AuthorizedKeysFile .ssh/authorized_keys
EOF

nv set system ssh-server permit-root-login prohibit-password
nv set system ssh-server strict disabled
nv_apply() {{
  # Prefer supported non-interactive flags if present.
  if nv config apply --assume-yes </dev/null 2>/dev/null; then
    return 0
  fi
  if nv config apply -y </dev/null 2>/dev/null; then
    return 0
  fi
  if nv config apply --yes </dev/null 2>/dev/null; then
    return 0
  fi

  # Fallback: feed a single 'y' without letting SIGPIPE from the writer fail the run.
  set +o pipefail
  printf 'y\n' | nv config apply
  rc=$?
  set -o pipefail
  return $rc
}}

nv_apply

# Restart SSH last to avoid impacting the current session mid-run.
systemctl restart ssh 2>/dev/null || systemctl restart sshd
ROOTSCRIPT
"""


def _configure_switch(
    switch: str,
    username: str,
    password: str,
    pubkey: str,
    *,
    timeout_s: int = 600,
) -> Result:
    remote_script = _remote_root_setup_script(pubkey=pubkey, sudopass=password)

    ssh_opts = [
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "PasswordAuthentication=yes",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "PreferredAuthentications=password",
        "-o",
        "PubkeyAuthentication=no",
        "-o",
        "NumberOfPasswordPrompts=1",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "ConnectionAttempts=1",
    ]

    argv = [
        "sshpass",
        "-p",
        password,
        "ssh",
        *ssh_opts,
        f"{username}@{switch}",
        "bash -s",
    ]
    rc, out, err = _run(argv, input_text=remote_script, timeout_s=timeout_s)
    return Result(switch=switch, ok=(rc == 0), stdout=out, stderr=err, returncode=rc)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Enable passwordless root access on Cumulus NVUE switches (via BCM headnode)."
    )
    parser.add_argument(
        "--switch",
        help="Comma-separated list of switch hostnames or IPs. If omitted, discover BCM switches that are UP.",
    )
    parser.add_argument("--username", required=True, help="SSH username for initial access.")
    parser.add_argument("--password", required=True, help="SSH password for initial access.")
    args = parser.parse_args(argv)

    pubkey = _read_pubkey()

    if args.switch:
        switches = _parse_switches_csv(args.switch)
    else:
        switches = _discover_up_switches_from_cmsh()

    if not switches:
        print("No switches found to configure.", file=sys.stderr)
        return 2

    failures: List[Result] = []
    for sw in switches:
        print(f"[{sw}] configuring...")
        res = _configure_switch(sw, args.username, args.password, pubkey)
        if res.ok:
            print(f"[{sw}] OK")
        else:
            print(f"[{sw}] FAILED (rc={res.returncode})", file=sys.stderr)
            failures.append(res)

    if failures:
        print("\nFailures:", file=sys.stderr)
        for f in failures:
            print(f"- {f.switch}: rc={f.returncode}", file=sys.stderr)
            if f.stderr.strip():
                print(f"  stderr:\n{f.stderr.rstrip()}", file=sys.stderr)
            elif f.stdout.strip():
                print(f"  stdout:\n{f.stdout.rstrip()}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


