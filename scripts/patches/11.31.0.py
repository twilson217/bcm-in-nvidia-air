#!/usr/bin/env python3
"""
BCM 11.31.0 patch: update installer110 Ubuntu 24.04 selection(s) to use Slurm 25.05.

Why:
  Some BCM 11.31.0 ISO/repo layouts appear to make slurm24.11 unavailable in the enabled
  repo set while slurm25.05 is available. The installer110 selection XMLs for Ubuntu 24.04
  currently request slurm24.11*, causing the head-node HPC package install to fail.

What this does:
  - Finds:  roles/head_node/files/buildmaster/selections/UBUNTU2404-*/extrapackages.xml
  - Replaces (idempotent):  slurm24.11  ->  slurm25.05
"""

from __future__ import annotations

import argparse
from pathlib import Path


def patch_file(path: Path) -> tuple[int, bool]:
    """Return (replacement_count, changed)."""
    original = path.read_text(encoding="utf-8", errors="strict")
    count = original.count("slurm24.11")
    if count == 0:
        return 0, False
    updated = original.replace("slurm24.11", "slurm25.05")
    if updated == original:
        return 0, False
    path.write_text(updated, encoding="utf-8")
    return count, True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection-dir", required=True, help="Installed Ansible collection directory")
    args = ap.parse_args()

    col_dir = Path(args.collection_dir)
    if not col_dir.exists() or not col_dir.is_dir():
        print(f"✗ Invalid --collection-dir: {col_dir}")
        return 2

    # Scope: Ubuntu 24.04 selection XMLs (CM/DIST/etc.) that define extra packages.
    selection_glob = "roles/head_node/files/buildmaster/selections/UBUNTU2404-*/extrapackages.xml"
    files = sorted(col_dir.glob(selection_glob))

    if not files:
        print(f"ℹ No Ubuntu 24.04 selection files found at: {selection_glob}")
        return 0

    total_replacements = 0
    changed_files = []

    for f in files:
        try:
            count, changed = patch_file(f)
            if count:
                total_replacements += count
            if changed:
                changed_files.append(f)
        except Exception as e:
            print(f"⚠ Failed to patch {f}: {e}")

    if total_replacements == 0:
        print("✓ No slurm24.11 references found (already patched or not applicable)")
        return 0

    # Keep output concise but actionable.
    print(f"✓ Replaced slurm24.11 -> slurm25.05 ({total_replacements} occurrence(s))")
    for f in changed_files:
        rel = f.relative_to(col_dir)
        print(f"  - patched: {rel}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


