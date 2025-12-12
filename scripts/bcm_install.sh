#!/usr/bin/env bash
# BCM Installation Script for NVIDIA Air
# This script runs LOCALLY on the BCM head node
# Placeholders are replaced by deploy_bcm_air.py before upload:
#   __PASSWORD__     - User's configured password
#   __PRODUCT_KEY__  - BCM license key
#   __BCM_VERSION__  - 10 or 11
#   __ADMIN_EMAIL__  - Admin email address

set -euo pipefail

# Configuration (populated by deploy_bcm_air.py)
BCM_PASSWORD="__PASSWORD__"
BCM_PRODUCT_KEY="__PRODUCT_KEY__"
BCM_VERSION="__BCM_VERSION__"
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
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git mysql-server rsync libldap2-dev libsasl2-dev
pip3 install --quiet --break-system-packages PyMySQL python-ldap
echo "  ✓ Dependencies installed"

# Workaround for Ubuntu 24.04 package conflict (BCM 10.24.x)
# libglapi-amber and libglapi-mesa are mutually exclusive
# The BCM Ansible collection tries to install both, causing failure
# Solution: Pre-install libglapi-mesa and hold the conflicting packages
echo "  Applying Ubuntu 24.04 package conflict workaround..."
apt-get install -y libglapi-mesa >/dev/null 2>&1 || true
# Hold the amber packages to prevent Ansible from installing them
apt-mark hold libglapi-amber libgl1-amber-dri >/dev/null 2>&1 || true
echo "  ✓ Package conflict workaround applied"

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

# Step 6: Clone/setup BCM Ansible installer
echo "[Step 6/10] Setting up BCM Ansible installer..."
BCM_INSTALLER_REPO="https://github.com/twilson217/bcm-ansible-installer.git"

if [ ! -d /home/ubuntu/bcm-ansible-installer ]; then
    echo "  Cloning bcm-ansible-installer from GitHub..."
    git clone "$BCM_INSTALLER_REPO" /home/ubuntu/bcm-ansible-installer
    if [ $? -ne 0 ]; then
        echo "  ✗ ERROR: Failed to clone bcm-ansible-installer"
        exit 1
    fi
    echo "  ✓ Repository cloned"
else
    echo "  ✓ bcm-ansible-installer already exists"
fi
cd /home/ubuntu/bcm-ansible-installer

# Fix playbook to use correct BCM version role
sed -i "s/brightcomputing\.installer110\.head_node/${BCM_ROLE}/g" playbook.yml
sed -i "s/brightcomputing\.installer100\.head_node/${BCM_ROLE}/g" playbook.yml
echo "  ✓ Playbook configured for ${BCM_ROLE}"

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

