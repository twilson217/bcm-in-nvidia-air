#!/usr/bin/env bash
# BCM Installation Script for NVIDIA Air
# This script runs LOCALLY on the BCM head node
# Placeholders are replaced by deploy_bcm_air.py before upload:
#   __PASSWORD__        - User's configured password
#   __PRODUCT_KEY__     - BCM license key
#   __BCM_VERSION__     - Major version (10 or 11)
#   __BCM_FULL_VERSION__- Full version (e.g., 10.24.03, 10.30.0, 11.0.0)
#   __ADMIN_EMAIL__     - Admin email address

set -euo pipefail

# Configuration (populated by deploy_bcm_air.py)
BCM_PASSWORD="__PASSWORD__"
BCM_PRODUCT_KEY="__PRODUCT_KEY__"
BCM_VERSION="__BCM_VERSION__"
BCM_FULL_VERSION="__BCM_FULL_VERSION__"
BCM_ADMIN_EMAIL="__ADMIN_EMAIL__"
BCM_EXTERNAL_INTERFACE="__EXTERNAL_INTERFACE__"  # Connected to outbound (DHCP)
BCM_MANAGEMENT_INTERFACE="__MANAGEMENT_INTERFACE__"  # Connected to oob-mgmt-switch (192.168.200.254)
BCM_ISO_PATH="/home/ubuntu/bcm.iso"
BCM_MOUNT_PATH="/mnt/dvd"  # Ansible mounts ISO here

# Determine collection name based on version
if [ "$BCM_VERSION" == "11" ]; then
    BCM_COLLECTION="brightcomputing.installer110"
    BCM_ROLE="brightcomputing.installer110.head_node"
else
    BCM_COLLECTION="brightcomputing.installer100"
    BCM_ROLE="brightcomputing.installer100.head_node"
fi

echo "=============================================="
echo "BCM ${BCM_VERSION} Installation Script"
echo "=============================================="
echo "Starting at: $(date)"
echo ""

# Step 1: Disable unattended upgrades (prevents apt conflicts)
echo "[Step 1/10] Disabling unattended upgrades..."
systemctl stop unattended-upgrades 2>/dev/null || true
systemctl disable unattended-upgrades 2>/dev/null || true
if [ -f /etc/apt/apt.conf.d/20auto-upgrades ]; then
    sed -i 's/Unattended-Upgrade "1"/Unattended-Upgrade "0"/g' /etc/apt/apt.conf.d/20auto-upgrades
fi
echo "  ✓ Unattended upgrades disabled"

# Step 2: Force IPv4 for apt (Air network can have IPv6 issues)
echo "[Step 2/10] Configuring apt for IPv4..."
echo 'Acquire::ForceIPv4 "true";' > /etc/apt/apt.conf.d/99force-ipv4
echo "  ✓ IPv4 forced for apt"

# Step 3: Update system and install dependencies
echo "[Step 3/10] Installing system dependencies..."

# Make apt fully non-interactive (no prompts for config file changes, etc.)
export DEBIAN_FRONTEND=noninteractive

apt-get update -qq

# Upgrade all packages to latest versions (important for BCM 10.24.x compatibility)
echo "  Upgrading system packages..."
apt-get -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold upgrade -y -qq

# Ensure we never keep both libglapi-amber and libglapi-mesa installed together.
# On Ubuntu 24.04 these packages conflict and can break apt.
echo "  Checking for libglapi-amber/libglapi-mesa conflicts..."
if dpkg -s libglapi-amber >/dev/null 2>&1 && dpkg -s libglapi-mesa >/dev/null 2>&1; then
    echo "  ⚠ Both libglapi-amber and libglapi-mesa are installed; removing libglapi-mesa..."
    apt-get remove -y -qq libglapi-mesa >/dev/null 2>&1 || true
fi

# Fix any broken dependencies
apt-get -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold --fix-broken install -y -qq || true

apt-get -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold install -y -qq python3 python3-pip python3-venv git mysql-server rsync libldap2-dev libsasl2-dev
pip3 install --quiet --break-system-packages PyMySQL python-ldap
echo "  ✓ Dependencies installed"

# Step 4: Secure MySQL installation
echo "[Step 4/10] Securing MySQL..."
# Start MySQL if not running
systemctl start mysql || true
systemctl enable mysql || true

# Secure MySQL with automated responses
mysql_secure_installation <<EOF

y
y
${BCM_PASSWORD}
${BCM_PASSWORD}
y
n
y
y
EOF
echo "  ✓ MySQL secured"

# Step 5: Verify BCM ISO exists (Ansible will mount it)
echo "[Step 5/10] Verifying BCM ISO..."
if [ ! -f "$BCM_ISO_PATH" ]; then
    echo "  ✗ ERROR: BCM ISO not found at $BCM_ISO_PATH"
    exit 1
fi
echo "  ✓ ISO found at $BCM_ISO_PATH (Ansible will handle mounting)"

# Step 6: Create BCM Ansible installer (self-contained, no external repo needed)
echo "[Step 6/10] Setting up BCM Ansible installer..."
BCM_INSTALLER_DIR="/home/ubuntu/bcm-ansible-installer"
mkdir -p "${BCM_INSTALLER_DIR}/inventory"
mkdir -p "${BCM_INSTALLER_DIR}/group_vars/head_node"
cd "${BCM_INSTALLER_DIR}"

# Create ansible.cfg
cat > ansible.cfg <<'ANSIBLECFG'
[defaults]
host_key_checking = False
inventory = inventory/hosts
deprecation_warnings = False
interpreter_python = auto_silent

[privilege_escalation]
become = True
become_method = sudo
become_user = root
ANSIBLECFG
echo "  ✓ ansible.cfg created"

# Create inventory/hosts
cat > inventory/hosts <<'INVENTORY'
[head_node]
localhost ansible_connection=local
INVENTORY
echo "  ✓ inventory/hosts created"

# Create requirements-control-node.txt
cat > requirements-control-node.txt <<'REQUIREMENTS'
ansible>=2.15
PyMySQL
python-ldap
REQUIREMENTS
echo "  ✓ requirements-control-node.txt created"

# Create playbook.yml with the correct role for this BCM version
cat > playbook.yml <<PLAYBOOK
---
- name: Install BCM Head Node
  hosts: head_node
  become: true
  roles:
    - ${BCM_ROLE}
  tasks:
    - name: Include post install user tasks
      include_tasks: post_install_user_tasks.yml
      when: post_install_user_tasks is defined or (lookup('file', 'post_install_user_tasks.yml', errors='ignore') | length > 0)
PLAYBOOK
echo "  ✓ playbook.yml created for ${BCM_ROLE}"

# Create Python virtual environment
python3 -m venv venv
source venv/bin/activate
pip install --quiet -r requirements-control-node.txt
echo "  ✓ Ansible installer ready"

# Step 7: Install BCM Ansible Galaxy collection
echo "[Step 7/10] Installing Ansible Galaxy collection: ${BCM_COLLECTION}..."
export ANSIBLE_LOG_PATH=/home/ubuntu/ansible_bcm_install.log
# Install our specific collection version (not whatever is in requirements.yml)
ansible-galaxy collection install "${BCM_COLLECTION}" --force
echo "  ✓ Collection installed: ${BCM_COLLECTION}"

#
# BCM 10.x on Ubuntu 24.04:
# The installer100 collection may attempt to install libglapi-mesa alongside libglapi-amber.
# Those packages conflict (mutually exclusive) on Ubuntu 24.04. To prevent this, we patch the
# installed Ansible collection in-place to remove references to libglapi-mesa before running
# the playbook. This ensures libglapi-amber is used for the head node installation.
# Note: Software images use libglapi-mesa via exclude_software_images_distro_packages.
#
patch_collection_remove_pkg() {
    local pkg="$1"
    local col_dir=""

    # Collection can be installed under root or ubuntu, depending on how this script is executed.
    local candidates=(
        "/root/.ansible/collections/ansible_collections/brightcomputing/${BCM_COLLECTION#brightcomputing.}"
        "/home/ubuntu/.ansible/collections/ansible_collections/brightcomputing/${BCM_COLLECTION#brightcomputing.}"
    )

    for d in "${candidates[@]}"; do
        if [ -d "$d" ]; then
            col_dir="$d"
            break
        fi
    done

    if [ -z "$col_dir" ]; then
        echo "  ⚠ Could not locate installed Ansible collection directory for ${BCM_COLLECTION}"
        return 0
    fi

    echo "  Patching collection at: ${col_dir}"

    # Find any files referencing the package and remove those lines.
    # This is intentionally blunt: if the collection is listing the package, we strip it.
    local files
    files="$(grep -RIl -- "${pkg}" "${col_dir}" || true)"
    if [ -z "$files" ]; then
        echo "  ✓ No ${pkg} references found in collection"
        return 0
    fi

    echo "  Removing ${pkg} references from collection files..."
    while IFS= read -r f; do
        sed -i "/${pkg}/d" "$f" || true
    done <<< "$files"

    if grep -RIl -- "${pkg}" "${col_dir}" >/dev/null 2>&1; then
        echo "  ⚠ ${pkg} still referenced after patch (continuing anyway)"
    else
        echo "  ✓ ${pkg} successfully removed from collection"
    fi
}

patch_collection_insert_ignore_errors_for_task() {
    local task_name="$1"
    local col_dir=""

    local candidates=(
        "/root/.ansible/collections/ansible_collections/brightcomputing/${BCM_COLLECTION#brightcomputing.}"
        "/home/ubuntu/.ansible/collections/ansible_collections/brightcomputing/${BCM_COLLECTION#brightcomputing.}"
    )

    for d in "${candidates[@]}"; do
        if [ -d "$d" ]; then
            col_dir="$d"
            break
        fi
    done

    if [ -z "$col_dir" ]; then
        echo "  ⚠ Could not locate installed Ansible collection directory for ${BCM_COLLECTION}"
        return 0
    fi

    local files
    files="$(grep -RIl -- "name: ${task_name}" "${col_dir}" || true)"
    if [ -z "$files" ]; then
        echo "  ℹ Task '${task_name}' not found in collection (no patch applied)"
        return 0
    fi

    echo "  Patching task '${task_name}' to ignore_errors (workaround for rc=-11 crashes)..."

    python3 - <<'PY'
import os
import sys

task_name = os.environ.get("TASK_NAME")
files = os.environ.get("FILES", "").splitlines()

def patch_file(path: str):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    out = []
    i = 0
    changed = False
    while i < len(lines):
        line = lines[i]

        # Cleanup: previous buggy patch inserted '- ignore_errors: true' as a new list item,
        # which breaks the playbook ("no module/action detected in task"). Remove it.
        if line.lstrip().startswith("- ignore_errors:"):
            changed = True
            i += 1
            continue

        out.append(line)

        if f"name: {task_name}" in line:
            # YAML task format is:
            # - name: ...
            #   <module>: ...
            #   ignore_errors: true
            #
            # We must NOT add a new list item ('- ignore_errors'), but a task attribute
            # aligned with other keys under the task.
            leading_ws = line[: len(line) - len(line.lstrip())]
            key_indent = leading_ws + "  "

            # If the next few lines already mention ignore_errors, don't duplicate.
            window = "".join(lines[i + 1 : i + 12])
            if "ignore_errors:" not in window:
                out.append(f"{key_indent}ignore_errors: true\n")
                changed = True
        i += 1

    if changed:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(out)

for p in files:
    p = p.strip()
    if not p:
        continue
    try:
        patch_file(p)
    except Exception:
        # Best-effort patching; don't fail installation because of patching.
        pass
PY
}

# BCM 10.x on Ubuntu 24.04: The installer100 collection references both libglapi-amber
# and libglapi-mesa which conflict. We remove libglapi-mesa references from the collection
# to ensure libglapi-amber is used during installation.
# Note: Software images use libglapi-mesa via exclude_software_images_distro_packages.
if [ "$BCM_VERSION" == "10" ]; then
    echo "  Applying Ansible collection patch (prevent libglapi-mesa install during BCM setup)..."
    patch_collection_remove_pkg "libglapi-mesa"
else
    echo "  Skipping libglapi collection patch for BCM ${BCM_VERSION} (installer110)"
fi

# Workaround for observed crashes during certificate generation (rc=-11 / SIGSEGV)
# If CMDaemon successfully writes cert.pem/cert.key but exits with rc=-11, Ansible fails.
# We treat that task as non-fatal; later tasks will still fail if certs truly aren't created.
export TASK_NAME="Generating webinterface certificate"
export FILES="$(grep -RIl -- "name: ${TASK_NAME}" /root/.ansible/collections/ansible_collections/brightcomputing/${BCM_COLLECTION#brightcomputing.} 2>/dev/null || true; grep -RIl -- "name: ${TASK_NAME}" /home/ubuntu/.ansible/collections/ansible_collections/brightcomputing/${BCM_COLLECTION#brightcomputing.} 2>/dev/null || true)"
patch_collection_insert_ignore_errors_for_task "${TASK_NAME}"


# Step 8: Create configuration files
echo "[Step 8/10] Creating BCM configuration..."

# Create group_vars directory structure
mkdir -p /home/ubuntu/bcm-ansible-installer/group_vars/head_node

# Create cluster-credentials.yml
cat > /home/ubuntu/bcm-ansible-installer/group_vars/head_node/cluster-credentials.yml <<CREDS
---
# Cluster credentials (auto-generated)
product_key: ${BCM_PRODUCT_KEY}
db_cmd_password: ${BCM_PASSWORD}
ldap_root_pass: ${BCM_PASSWORD}
ldap_readonly_pass: ${BCM_PASSWORD}
slurm_user_pass: ${BCM_PASSWORD}
mysql_login_user: root
mysql_login_password: ${BCM_PASSWORD}
mysql_login_unix_socket: /var/run/mysqld/mysqld.sock
CREDS

# Create cluster-settings.yml
# Interface mapping (detected from topology by deploy_bcm_air.py):
#   external_interface = outbound connection (for internet access via DHCP)
#   management_interface = oob-mgmt-switch connection (BCM internal network 192.168.200.0/24)
cat > /home/ubuntu/bcm-ansible-installer/group_vars/head_node/cluster-settings.yml <<SETTINGS
---
# General cluster settings (auto-generated from topology)
external_interface: ${BCM_EXTERNAL_INTERFACE}
external_ip_address: DHCP
management_interface: ${BCM_MANAGEMENT_INTERFACE}
management_ip_address: 192.168.200.254
management_network_baseaddress: 192.168.200.0
management_network_netmask: 24
install_medium: dvd
install_medium_dvd_path: "${BCM_ISO_PATH}"
timezone: UTC
# Workaround: prevent software-image creation from installing both libglapi-amber and libglapi-mesa.
# On Ubuntu 24.04, libglapi-amber conflicts with libglapi-mesa. The installer’s cm-create-image
# distro package list includes both mesa and amber-dri; excluding amber-dri avoids the conflict.
exclude_software_images_distro_packages:
  - libgl1-amber-dri
  - libglapi-amber
license:
  country: US
  state: California
  locality: Santa Clara
  organization: NVIDIA
  organizational_unit: ${BCM_ADMIN_EMAIL}
  cluster_name: bcm-air-lab
  mac: "{{ ansible_default_ipv4.macaddress }}"
SETTINGS
echo "  External interface: ${BCM_EXTERNAL_INTERFACE} (outbound/DHCP)"
echo "  Management interface: ${BCM_MANAGEMENT_INTERFACE} (oob-mgmt-switch/192.168.200.254)"

# Create post_install_user_tasks.yml for DNS fixes
cat > /home/ubuntu/bcm-ansible-installer/post_install_user_tasks.yml <<POSTTASKS
---
- name: Add DNSSEC validation configuration
  ansible.builtin.blockinfile:
    path: /etc/bind/named.conf.global.options.include
    block: |
      dnssec-validation no;
    marker: "# {mark} ANSIBLE MANAGED BLOCK - DNSSEC"
    create: yes
  register: dnssec_config

- name: Add Google DNS server configuration
  ansible.builtin.blockinfile:
    path: /etc/bind/named.conf.include
    block: |
      server 8.8.8.8 {
          edns no;
      };
    marker: "# {mark} ANSIBLE MANAGED BLOCK - Google DNS"
    create: yes
  register: google_dns_config

- name: Restart named service
  ansible.builtin.systemd:
    name: named
    state: restarted
  when: dnssec_config.changed or google_dns_config.changed
POSTTASKS

echo "  ✓ Configuration files created"

# Step 9: Run BCM Ansible playbook
echo "[Step 9/10] Running BCM installation playbook..."
echo "  This will take 30-45 minutes. Check /home/ubuntu/ansible_bcm_install.log for progress."
echo ""

cd /home/ubuntu/bcm-ansible-installer
source venv/bin/activate

ansible-playbook -i inventory/hosts playbook.yml 2>&1 | tee -a /home/ubuntu/ansible_bcm_install.log

# Check if installation succeeded
if grep -q "failed=0" /home/ubuntu/ansible_bcm_install.log; then
    echo ""
    echo "  ✓ BCM Ansible playbook completed successfully"
else
    echo ""
    echo "  ⚠ BCM installation may have had errors. Check /home/ubuntu/ansible_bcm_install.log"
fi

# Step 10: Post-installation configuration
echo "[Step 10/10] Post-installation configuration..."

# Enable TFTP for PXE boot
systemctl enable tftpd.socket 2>/dev/null || true
systemctl start tftpd.socket 2>/dev/null || true

# Set up BCM environment
export MODULES_USE_COMPAT_VERSION=1
export MODULEPATH=/cm/local/modulefiles:/cm/shared/modulefiles
if [ -f /cm/local/apps/environment-modules/current/init/bash ]; then
    source /cm/local/apps/environment-modules/current/init/bash
    module load cmsh 2>/dev/null || true
fi

# Configure lite daemon repo from ISO
if [ -x /cm/local/apps/cmd/sbin/cm-lite-daemon-repo ]; then
    /cm/local/apps/cmd/sbin/cm-lite-daemon-repo "$BCM_MOUNT_PATH" || true
fi

# Change BCM passwords (GUI, image root)
if [ -x /cm/local/apps/cmd/sbin/cm-change-passwd ]; then
    echo "  Changing BCM passwords..."
    (sleep 1; echo y; sleep 1; echo "${BCM_PASSWORD}"; sleep 1; echo "${BCM_PASSWORD}"; \
     sleep 1; echo y; sleep 1; echo "${BCM_PASSWORD}"; sleep 1; echo "${BCM_PASSWORD}"; \
     sleep 1; echo y; sleep 1; echo "${BCM_PASSWORD}"; sleep 1; echo "${BCM_PASSWORD}"; \
     sleep 1; echo n) | /cm/local/apps/cmd/sbin/cm-change-passwd 2>/dev/null || true
fi

echo "  ✓ Post-installation complete"

# Summary
echo ""
echo "=============================================="
echo "BCM ${BCM_VERSION} Installation Complete!"
echo "=============================================="
echo ""
echo "Access BCM:"
echo "  - CLI:  cmsh"
echo "  - GUI:  https://<bcm-ip>:8081/base-view"
echo "  - User: root"
echo "  - Pass: (your configured password)"
echo ""
echo "Log file: /home/ubuntu/ansible_bcm_install.log"
echo "Finished at: $(date)"
echo "=============================================="

