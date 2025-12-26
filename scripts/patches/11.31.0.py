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

  - Removes upstream installer110's Ubuntu 24.04 behavior of excluding libglapi-mesa
    from software-image creation and (optionally) from distro package exclusions.
    We standardize on libglapi-mesa and exclude amber instead.

  - Fixes DGX auto-detection so we only enable DGX repos when apt metadata exists.
    Some ISOs may contain an empty dgx-os directory (or no Packages index), which
    causes apt-get update inside cm-create-image to fail during repo validation.
"""

from __future__ import annotations

import argparse
import re
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


def patch_text_file_replace(path: Path, old: str, new: str) -> bool:
    """
    Replace exact substring old->new in a text file.
    Returns True if a change was written.
    """
    content = path.read_text(encoding="utf-8", errors="strict")
    if old not in content:
        return False
    updated = content.replace(old, new)
    if updated == content:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def patch_text_file_remove_block(path: Path, block: str) -> bool:
    """
    Remove an exact block of text (including newlines) if present.
    Returns True if a change was written.
    """
    content = path.read_text(encoding="utf-8", errors="strict")
    if block not in content:
        return False
    updated = content.replace(block, "")
    if updated == content:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def patch_dgx_stat_path_to_packages_gz(path: Path) -> bool:
    """
    Update any dgx-os stat path in an Ansible task file to require Packages.gz.

    This is intentionally tolerant of quote style / jinja formatting differences across
    collection versions. It only edits the provided file.
    """
    content = path.read_text(encoding="utf-8", errors="strict")
    # Only add /Packages.gz if it's not already present
    pattern = re.compile(r"(/data/packages/packagegroups/dgx-os)(?!/Packages\.gz)")
    updated, n = pattern.subn(r"\1/Packages.gz", content)
    if n == 0 or updated == content:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection-dir", required=True, help="Installed Ansible collection directory")
    args = ap.parse_args()

    col_dir = Path(args.collection_dir)
    if not col_dir.exists() or not col_dir.is_dir():
        print(f"✗ Invalid --collection-dir: {col_dir}")
        return 2

    total_changes = 0
    changed_files: list[Path] = []

    # 1) Slurm selection fix (Ubuntu 24.04 selection XMLs: CM/DIST/etc.)
    selection_glob = "roles/head_node/files/buildmaster/selections/UBUNTU2404-*/extrapackages.xml"
    files = sorted(col_dir.glob(selection_glob))

    if not files:
        print(f"ℹ No Ubuntu 24.04 selection files found at: {selection_glob}")
    else:
        total_replacements = 0
        for f in files:
            try:
                count, changed = patch_file(f)
                if count:
                    total_replacements += count
                if changed:
                    total_changes += 1
                    changed_files.append(f)
            except Exception as e:
                print(f"⚠ Failed to patch {f}: {e}")

        if total_replacements > 0:
            print(f"✓ Replaced slurm24.11 -> slurm25.05 ({total_replacements} occurrence(s))")
        else:
            print("✓ No slurm24.11 references found (already patched or not applicable)")

    # 2) Remove installer110 Ubuntu 24.04 software-image exclusion of libglapi-mesa
    # File: roles/head_node/tasks/post_install/software_images/main.yml
    software_images_main = col_dir / "roles/head_node/tasks/post_install/software_images/main.yml"
    if software_images_main.exists():
        block = (
            "- name: Set cm-create-image cli parameters (Non Airgapped) | Exclude libglapi-mesa package\n"
            "  set_fact:\n"
            "    exclude_software_images_distro_packages: \"{{ exclude_software_images_distro_packages + [ 'libglapi-mesa' ] }}\"\n"
            "  when: ansible_distribution == \"Ubuntu\" and ansible_distribution_version == \"24.04\"\n"
            "\n"
        )
        try:
            if patch_text_file_remove_block(software_images_main, block):
                total_changes += 1
                changed_files.append(software_images_main)
                print("✓ Removed Ubuntu 24.04 libglapi-mesa exclusion from software-image creation")
            else:
                print("✓ Software-image libglapi-mesa exclusion already absent")
        except Exception as e:
            print(f"⚠ Failed to patch {software_images_main}: {e}")
    else:
        print("ℹ Could not find software_images/main.yml to patch (unexpected layout)")

    # 3) Remove installer110 Ubuntu 24.04 distro exclusion of libglapi-mesa (if present)
    # File: roles/head_node/vars/os_Ubuntu_24.04_vars.yml
    os_ubuntu_2404_vars = col_dir / "roles/head_node/vars/os_Ubuntu_24.04_vars.yml"
    if os_ubuntu_2404_vars.exists():
        old = "    (['libglapi-mesa'] if not head_node_airgapped else [])"
        new = "    ([])"  # keep structure, but never exclude libglapi-mesa
        try:
            if patch_text_file_replace(os_ubuntu_2404_vars, old, new):
                total_changes += 1
                changed_files.append(os_ubuntu_2404_vars)
                print("✓ Removed Ubuntu 24.04 libglapi-mesa from distro exclusion list")
            else:
                print("✓ Distro exclusion of libglapi-mesa already absent or not matching expected pattern")
        except Exception as e:
            print(f"⚠ Failed to patch {os_ubuntu_2404_vars}: {e}")
    else:
        print("ℹ Could not find os_Ubuntu_24.04_vars.yml to patch (unexpected layout)")

    # 4) DGX auto-detection: require apt metadata, not just directory presence
    # The collection decides whether to enable DGX behavior based on a stat() check.
    # If it incorrectly sets dgx=True, dvd-debian-repos.j2 adds a dgx-os repo that
    # doesn't have an index (Packages/Packages.gz), and cm-create-image fails at:
    #   E: Failed to fetch .../packagegroups/dgx-os/./Packages (No such file...)
    #
    # Change the stat path from the directory to Packages.gz so dgx is only enabled
    # when the repo is actually usable by apt.
    bright_iso_mount = col_dir / "roles/bright_iso/tasks/mount.yml"
    if bright_iso_mount.exists():
        try:
            if patch_dgx_stat_path_to_packages_gz(bright_iso_mount):
                total_changes += 1
                changed_files.append(bright_iso_mount)
                print("✓ Tightened DGX ISO detection to require Packages.gz")
            else:
                print("✓ DGX ISO detection already uses Packages.gz (or pattern mismatch)")
        except Exception as e:
            print(f"⚠ Failed to patch {bright_iso_mount}: {e}")
    else:
        print("ℹ Could not find bright_iso/tasks/mount.yml to patch (unexpected layout)")

    head_node_setup_dgx = col_dir / "roles/head_node/tasks/setup_dgx.yml"
    if head_node_setup_dgx.exists():
        try:
            if patch_dgx_stat_path_to_packages_gz(head_node_setup_dgx):
                total_changes += 1
                changed_files.append(head_node_setup_dgx)
                print("✓ Tightened head_node DGX checks to require Packages.gz")
            else:
                print("✓ head_node DGX checks already use Packages.gz (or pattern mismatch)")
        except Exception as e:
            print(f"⚠ Failed to patch {head_node_setup_dgx}: {e}")
    else:
        print("ℹ Could not find head_node/tasks/setup_dgx.yml to patch (unexpected layout)")

    if total_changes == 0:
        print("✓ No changes needed")
        return 0

    # Keep output concise but actionable.
    for f in changed_files:
        try:
            rel = f.relative_to(col_dir)
        except Exception:
            rel = f
        print(f"  - patched: {rel}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


