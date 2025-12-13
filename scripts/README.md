# Scripts

This directory contains utility scripts for the BCM NVIDIA Air deployment automation.

## Core Scripts

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

**Auto-setup**: If `.env` or `.iso/` directory is missing, the script creates them automatically.

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
3. Applies Ubuntu 24.04 workaround for BCM 10.x (libglapi-amber/mesa conflict)
4. Secures MySQL installation
5. Clones bcm-ansible-installer from GitHub
6. Installs Bright Computing Ansible Galaxy collection
7. Patches collection for compatibility issues
8. Generates configuration files (`cluster-settings.yml`, `cluster-credentials.yml`)
9. Runs the BCM Ansible playbook
10. Performs post-install configuration (TFTP, passwords)

**Note**: Do not run this script directly. It's designed to be executed by `deploy_bcm_air.py`.

---

### `test-loop.py`
**Purpose**: Run an overnight test matrix across multiple BCM versions and Air sites.

**Usage**:
```bash
# Run all 6 tests
python scripts/test-loop.py

# Run specific tests
python scripts/test-loop.py --test2 --test3 --test4

# Dry run (show what would run)
python scripts/test-loop.py --dry-run

# Stop on first failure
python scripts/test-loop.py --stop-on-fail
```

**Test matrix**:
| Test | BCM Version | Air Site |
|------|-------------|----------|
| test1 | 10.25.03 | air.nvidia.com |
| test2 | 10.30.0 | air.nvidia.com |
| test3 | 11.x | air.nvidia.com |
| test4 | 10.25.03 | air-inside.nvidia.com |
| test5 | 10.30.0 | air-inside.nvidia.com |
| test6 | 11.x | air-inside.nvidia.com |

**Features**:
- Tracks elapsed time per test and total loop time
- Streams output to console and `.logs/deploy_bcm_air.log`
- Writes summary to `.logs/test-summary.log`
- Automatically deletes simulations after each test (cleanup)
- Parses simulation ID from output for reliable cleanup

---

## Utility Scripts

### `delete-sim.py`
**Purpose**: Delete an NVIDIA Air simulation by ID or name.

**Usage**:
```bash
# Delete by ID
python scripts/delete-sim.py --sim-id 16514465-7187-432a-9beb-b3f88556a01a

# Delete by name
python scripts/delete-sim.py --sim-name "202512001-BCM-Lab"

# Use specific env file
python scripts/delete-sim.py --sim-id <uuid> --env .env.external

# Use internal Air
python scripts/delete-sim.py --sim-id <uuid> --internal

# Dry run
python scripts/delete-sim.py --sim-id <uuid> --dry-run
```

---

### `setup_userconfig.py`
**Purpose**: One-time setup to create the cloud-init UserConfig.

**Usage**:
```bash
python scripts/setup_userconfig.py
```

UserConfigs are user-level resources (not simulation-specific), so they can be created once and reused. This script is useful if:
- The UserConfig API is rate-limited during full deployment
- You want to pre-configure before running deployments

---

### `cleanup_userconfigs.py`
**Purpose**: List and delete duplicate or test UserConfigs.

**Usage**:
```bash
# List all configs
python scripts/cleanup_userconfigs.py

# Delete duplicates (keeps 'bcm-cloudinit-password')
python scripts/cleanup_userconfigs.py --delete
```

Useful for cleaning up after multiple test runs.

---

## Troubleshooting Scripts

For scripts used to debug NVIDIA Air API issues (authentication, UserConfig creation, WAF patterns), see:

**[`air-tests/README.md`](air-tests/README.md)**

These scripts are not part of the normal deployment workflow but are preserved for future troubleshooting.
