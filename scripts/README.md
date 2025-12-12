# Scripts

This directory contains utility scripts for the BCM NVIDIA Air deployment automation.

## Main Scripts

### `check_setup.py`
**Purpose**: Verify all prerequisites before running a deployment.

**Usage**:
```bash
python scripts/check_setup.py
```

**What it checks**:
- Python version (3.10+)
- `uv` package manager installed
- All required `.env` variables configured:
  - `AIR_API_TOKEN`, `AIR_USERNAME`
  - `SSH_PRIVATE_KEY`, `SSH_PUBLIC_KEY` (verifies files exist)
  - `BCM_PRODUCT_KEY`
- BCM ISO file present in `.iso/` directory

**Auto-setup**: If `.env` or `.iso/` directory is missing, the script creates them automatically (copies `sample-configs/env.example` to `.env`).

---

### `topology_validation.py`
**Purpose**: Validate topology JSON files against BCM deployment requirements.

**Usage**:
```bash
python scripts/topology_validation.py topologies/default.json
python scripts/topology_validation.py topologies/*.json  # Validate multiple
```

**What it validates**:
- BCM node exists (name starts with `bcm`)
- BCM node's `eth0` is connected to `"outbound"` (required for NVIDIA Air)
- BCM node has interface connected to `oob-mgmt-switch` (for management network)
- OOB management is disabled (`"oob": false`)
- PXE boot nodes are properly configured

---

### `bcm_install.sh`
**Purpose**: Install BCM on the head node. This script runs **remotely on the BCM VM**, not locally.

**How it works**:
1. `deploy_bcm_air.py` uploads this script to the BCM head node
2. Placeholders (`__PASSWORD__`, `__BCM_VERSION__`, etc.) are replaced with actual values
3. The script is executed via SSH

**What it does**:
1. Disables unattended upgrades
2. Installs system dependencies (Python, MySQL, etc.)
3. Secures MySQL installation
4. Clones [bcm-ansible-installer](https://github.com/twilson217/bcm-ansible-installer)
5. Installs Bright Computing Ansible Galaxy collection
6. Generates configuration files (`cluster-settings.yml`, `cluster-credentials.yml`)
7. Runs the BCM Ansible playbook
8. Performs post-install configuration (TFTP, passwords)

**Note**: Do not run this script directly. It's designed to be executed by `deploy_bcm_air.py`.

---

### `cumulus-ztp.sh`
**Purpose**: Zero Touch Provisioning script for Cumulus Linux switches.

**Usage**: Uploaded to BCM and served via HTTP for switch auto-configuration.

**What it does**:
- Sets up SSH key authentication
- Changes default `cumulus` user password
- Configures passwordless sudo
- Enables Debian package sources
- Sets pre-login banner

**Note**: This script is a template. Future automation will customize it for specific deployments.

---

## Debug/Test Scripts

These scripts are useful for troubleshooting authentication and API issues.

### `test_auth.sh`
**Purpose**: Quick bash script to test NVIDIA Air API authentication via curl.

**Usage**:
```bash
# Test external Air
./scripts/test_auth.sh

# Test internal Air (requires VPN)
./scripts/test_auth.sh --internal
```

---

### `test_sdk_auth.py`
**Purpose**: Test authentication using the Air SDK Python library.

**Usage**:
```bash
python scripts/test_sdk_auth.py
```

**What it tests**:
- Air SDK package is installed
- API token authentication works
- Can list simulations

---

### `test_direct_auth.py`
**Purpose**: Test direct API authentication using the `requests` library (without Air SDK).

**Usage**:
```bash
python scripts/test_direct_auth.py
```

Useful for debugging when the SDK has issues.

---

### `check_sim_state.py`
**Purpose**: Debug script to check the state of a specific simulation.

**Usage**:
```bash
python scripts/check_sim_state.py <simulation_id>
```

**What it shows**:
- Simulation status
- Node states
- Service information

