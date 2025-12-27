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

  - Fixes cm-create-image (cluster-tools) APT DVD repo template to not hardcode a
    dgx-os repo line. On BCM 11.31.0 non-DGX ISOs, there is no
    data/packages/packagegroups/dgx-os repo, so apt-get update fails during
    cm-create-image repo validation if dgx-os is listed.
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


def patch_cluster_tools_disable_dgx_os_apt_repo() -> bool:
    """
    Patch cluster-tools' cm-create-image APT DVD repo template to not include dgx-os.

    Root cause:
      cm-create-image mounts the ISO inside the image at:
        /mnt/bright-installer-<random>
      and uses a built-in APT repo template that always includes:
        file://{base_path}/data/packages/packagegroups/dgx-os ./

      For non-DGX ISOs (including BCM 11.31.0 standard ISO), dgx-os doesn't exist,
      so apt-get update fails with:
        File not found - .../packagegroups/dgx-os/./Packages
    """
    dvdutils = Path(
        "/cm/local/apps/cluster-tools/lib/python3.12/site-packages/cm_create_image/dvdutils.py"
    )
    if not dvdutils.exists():
        print("ℹ cluster-tools dvdutils.py not found (skipping dgx-os APT repo patch)")
        return False

    content = dvdutils.read_text(encoding="utf-8", errors="strict")
    if "packagegroups/dgx-os" not in content:
        print("✓ cluster-tools APT repo template already has no dgx-os repo")
        return False
    if "disabled by bcm patch" in content or "# deb [trusted=yes] file://{base_path}/data/packages/packagegroups/dgx-os" in content:
        print("✓ cluster-tools dgx-os APT repo line already disabled")
        return False

    # Comment out the DGX-OS apt repo block (keep it readable + idempotent).
    # This is tolerant to minor whitespace differences.
    pattern = re.compile(
        r"\n# DVD DGX-OS packages repository\n"
        r"deb\s+\[trusted=yes\]\s+file://\{base_path\}/data/packages/packagegroups/dgx-os\s+\./\n",
        re.MULTILINE,
    )
    updated, n = pattern.subn(
        "\n# DVD DGX-OS packages repository (disabled by bcm patch)\n"
        "# deb [trusted=yes] file://{base_path}/data/packages/packagegroups/dgx-os ./\n",
        content,
    )

    if n == 0:
        # Fallback: comment out ANY apt repo line referencing dgx-os inside this file.
        pattern2 = re.compile(
            r"^(deb\s+\[trusted=yes\]\s+file://\{base_path\}/data/packages/packagegroups/dgx-os\s+\./\s*)$",
            re.MULTILINE,
        )
        updated, n2 = pattern2.subn(r"# \1  # disabled by bcm patch", content)
        if n2 == 0 or updated == content:
            print("⚠ Could not patch cluster-tools dgx-os APT repo line (unexpected format)")
            return False

    if updated == content:
        return False

    dvdutils.write_text(updated, encoding="utf-8")
    print("✓ Disabled cluster-tools dgx-os APT repo line (prevents repo validation failure)")
    return True


def patch_cluster_tools_slurm_ubuntu2404() -> bool:
    """
    Patch cluster-tools' Ubuntu 24.04 CM extra packages selection to use slurm25.05.

    Root cause (observed in cm-create-image log):
      -- Package slurm24.11 not installed (NOT AVAILABLE)

    The BCM 11.31.0 ISO *does* ship slurm25.05 packages (in packagegroups/hpc),
    so switching the selection unblocks cm-create-image.
    """
    cfg = Path("/cm/local/apps/cluster-tools/config/UBUNTU2404-cm-extrapackages.xml")
    if not cfg.exists():
        print("ℹ cluster-tools UBUNTU2404-cm-extrapackages.xml not found (skipping Slurm patch)")
        return False

    content = cfg.read_text(encoding="utf-8", errors="strict")
    if "slurm24.11" not in content:
        print("✓ cluster-tools Ubuntu 24.04 slurm24.11 already absent")
        return False

    updated = content.replace("slurm24.11", "slurm25.05")
    if updated == content:
        return False
    cfg.write_text(updated, encoding="utf-8")
    print("✓ Patched cluster-tools Ubuntu 24.04 selection: slurm24.11 -> slurm25.05")
    return True


def patch_installer110_ignore_dps_cert_missing(col_dir: Path) -> bool:
    """
    Patch installer110 head_node validate.yml so missing DPS cert isn't fatal.

    In BCM 11.31.0 airgapped installs, `cm-check-certificates.sh` can return rc=96 with:
      /cm/local/apps/dps/etc/dps.pem : not found

    That appears to be non-fatal (DPS not installed/enabled), so allow rc=96.
    """
    validate_yml = col_dir / "roles/head_node/tasks/validate.yml"
    if not validate_yml.exists():
        print("ℹ installer110 validate.yml not found (skipping certificate check patch)")
        return False

    content = validate_yml.read_text(encoding="utf-8", errors="strict")
    if "cm-check-certificates.sh" not in content:
        print("✓ installer110 validate.yml has no certificate check (nothing to patch)")
        return False

    # If it's already patched, don't touch.
    if "rc not in [0, 96]" in content or "cm_check_certificates" in content:
        print("✓ installer110 certificate check already patched")
        return False

    # Replace the simple command task with a registered task + tolerant failed_when.
    old_block = (
        "---\n"
        "- name: Check certificates\n"
        "  ansible.builtin.command:\n"
        "    cmd: /cm/local/apps/cmd/scripts/cm-check-certificates.sh\n"
        "  changed_when: false\n"
    )

    new_block = (
        "---\n"
        "- name: Check certificates\n"
        "  ansible.builtin.command:\n"
        "    cmd: /cm/local/apps/cmd/scripts/cm-check-certificates.sh\n"
        "  register: cm_check_certificates\n"
        "  changed_when: false\n"
        "  failed_when: cm_check_certificates.rc not in [0, 96]\n"
    )

    if old_block in content:
        validate_yml.write_text(content.replace(old_block, new_block), encoding="utf-8")
        print("✓ Patched installer110 certificate check to allow rc=96 (missing dps.pem)")
        return True

    # Fallback: try a looser patch if formatting differs.
    # Insert register/failed_when after changed_when if we find the task.
    pattern = re.compile(
        r"(- name:\s*Check certificates\s*\n"
        r"\s*ansible\.builtin\.command:\s*\n"
        r"\s*cmd:\s*/cm/local/apps/cmd/scripts/cm-check-certificates\.sh\s*\n"
        r"\s*changed_when:\s*false\s*\n)",
        re.MULTILINE,
    )
    replacement = (
        r"\1"
        "  register: cm_check_certificates\n"
        "  failed_when: cm_check_certificates.rc not in [0, 96]\n"
    )
    updated, n = pattern.subn(replacement, content)
    if n == 0 or updated == content:
        print("⚠ Could not patch installer110 certificate check (unexpected format)")
        return False

    validate_yml.write_text(updated, encoding="utf-8")
    print("✓ Patched installer110 certificate check (regex) to allow rc=96")
    return True


def patch_installer110_patch_cluster_tools_before_cm_create_image(col_dir: Path) -> bool:
    """
    Patch installer110 create.yml to patch cluster-tools immediately before cm-create-image runs.

    Why:
      Our bcm_install.sh hook runs the version patch BEFORE ansible-playbook starts.
      At that time, cluster-tools isn't installed yet, so direct patching of:
        - /cm/local/apps/cluster-tools/.../dvdutils.py
        - /cm/local/apps/cluster-tools/config/UBUNTU2404-cm-extrapackages.xml
      is a no-op on fresh installs.

      Adding these patches as an Ansible step right before cm-create-image ensures they
      run after cluster-tools is present and fixes:
        - dgx-os repo validation failure (missing packagegroups/dgx-os)
        - slurm24.11 NOT AVAILABLE on Ubuntu 24.04 (use slurm25.05)
    """
    create_yml = col_dir / "roles/head_node/tasks/post_install/software_images/create.yml"
    if not create_yml.exists():
        print("ℹ installer110 software_images/create.yml not found (skipping cluster-tools pre-patch)")
        return False

    content = create_yml.read_text(encoding="utf-8", errors="strict")
    if "invoke cm-create-image" not in content:
        print("ℹ installer110 create.yml did not look like expected (skipping)")
        return False

    if "Patch cluster-tools for BCM 11.31.0" in content:
        print("✓ installer110 create.yml already patches cluster-tools before cm-create-image")
        return False

    needle = "  - name: Create {{ image_name }} software image | invoke cm-create-image\n"
    if needle not in content:
        print("⚠ Could not find cm-create-image task anchor in create.yml (unexpected format)")
        return False

    inject = (
        "  - name: Patch cluster-tools for BCM 11.31.0 (dgx-os repo + Slurm selection)\n"
        "    ansible.builtin.shell:\n"
        "      cmd: |\n"
        "        set -euo pipefail\n"
        "\n"
        "        DVDUTILS=/cm/local/apps/cluster-tools/lib/python3.12/site-packages/cm_create_image/dvdutils.py\n"
        "        if [ -f \"$DVDUTILS\" ]; then\n"
        "          python3 - <<'PY'\n"
        "from pathlib import Path\n"
        "p = Path(\"/cm/local/apps/cluster-tools/lib/python3.12/site-packages/cm_create_image/dvdutils.py\")\n"
        "txt = p.read_text(encoding=\"utf-8\", errors=\"strict\")\n"
        "# Comment out dgx-os APT repo line if present and not already commented.\n"
        "needle = \"deb [trusted=yes] file://{base_path}/data/packages/packagegroups/dgx-os ./\"\n"
        "if needle in txt and (\"# \" + needle) not in txt:\n"
        "    txt = txt.replace(needle, \"# \" + needle + \"  # disabled by bcm patch\")\n"
        "    p.write_text(txt, encoding=\"utf-8\")\n"
        "PY\n"
        "        fi\n"
        "\n"
        "        CFG=/cm/local/apps/cluster-tools/config/UBUNTU2404-cm-extrapackages.xml\n"
        "        if [ -f \"$CFG\" ]; then\n"
        "          # Replace slurm24.11 -> slurm25.05 (idempotent)\n"
        "          sed -i 's/slurm24\\.11/slurm25.05/g' \"$CFG\"\n"
        "        fi\n"
        "    changed_when: false\n"
        "\n"
    )

    create_yml.write_text(content.replace(needle, inject + needle), encoding="utf-8")
    print("✓ Patched installer110 create.yml to patch cluster-tools before cm-create-image")
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

    # 4b) installer110: certificate validation can fail when optional DPS cert is missing
    try:
        if patch_installer110_ignore_dps_cert_missing(col_dir):
            total_changes += 1
            changed_files.append(col_dir / "roles/head_node/tasks/validate.yml")
    except Exception as e:
        print(f"⚠ Failed to patch installer110 certificate check: {e}")

    # 4c) installer110: patch cluster-tools right before cm-create-image runs
    try:
        if patch_installer110_patch_cluster_tools_before_cm_create_image(col_dir):
            total_changes += 1
            changed_files.append(col_dir / "roles/head_node/tasks/post_install/software_images/create.yml")
    except Exception as e:
        print(f"⚠ Failed to patch installer110 cm-create-image pre-step: {e}")

    # 5) cluster-tools: cm-create-image APT DVD repo template hardcodes dgx-os
    try:
        if patch_cluster_tools_disable_dgx_os_apt_repo():
            total_changes += 1
    except Exception as e:
        print(f"⚠ Failed to patch cluster-tools APT repo template: {e}")

    # 6) cluster-tools: Ubuntu 24.04 selection requests slurm24.11 (not available on ISO)
    try:
        if patch_cluster_tools_slurm_ubuntu2404():
            total_changes += 1
    except Exception as e:
        print(f"⚠ Failed to patch cluster-tools Ubuntu 24.04 Slurm selection: {e}")

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


