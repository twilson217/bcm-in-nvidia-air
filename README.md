<!-- AIR:tour -->

# Automated BCM Deployment on NVIDIA Air

This repository provides automated deployment of Bright Cluster Manager (BCM) on NVIDIA Air using stock Ubuntu 24.04 images. No custom image creation required - just bring your BCM ISO!

## Overview

This solution automates the complete BCM deployment process:
- Creates NVIDIA Air simulation from topology definition
- Uploads your BCM ISO to the head node via rsync
- Installs BCM 10.x or 11.x using official Ansible Galaxy collections
- Configures network interfaces and storage automatically
- Sets up basic BCM configuration (passwords, DNS, TFTP)

**Key Benefits:**
- No custom image creation or upload required
- Uses stock Ubuntu 24.04 images available in Air
- Choose BCM 10.x or 11.x at deployment time
- Fully automated via NVIDIA Air APIs
- Reliable ISO upload with rsync (resume support)
- Complete deployment in ~45-60 minutes (mostly unattended)

**External Dependencies:**
- [brightcomputing.installer100](https://galaxy.ansible.com/ui/repo/published/brightcomputing/installer100/) - Ansible Galaxy collection for BCM 10.x
- [brightcomputing.installer110](https://galaxy.ansible.com/ui/repo/published/brightcomputing/installer110/) - Ansible Galaxy collection for BCM 11.x

**Included Submodule:**
- `bcm-ansible-installer/` - Ansible scaffolding for BCM installation (included as a git submodule)

**Note on Free Tier:** The external air.nvidia.com site may have limitations on free accounts (e.g., cloud-init/UserConfig may not be available). The script includes fallback mechanisms for password and SSH key configuration.

## Quick Start

### Prerequisites

1. **NVIDIA Air account** with API access
   - External site: [air.nvidia.com](https://air.nvidia.com) - publicly accessible
   - Internal site (NVIDIA employees): [air-inside.nvidia.com](https://air-inside.nvidia.com) - **requires NVIDIA VPN or internal network**
2. **Python 3.10+** installed locally
3. **NVIDIA Air API token** (generate from your Air account settings)
4. **BCM ISO file** (~5GB) - Download from [Bright Computing Customer Portal](https://customer.brightcomputing.com/download-iso)
   - Requires your BCM product key
   - Download the Ubuntu 24.04 version matching your desired BCM version (10.x or 11.x)
5. **BCM Product Key** - [Request a Free BCM License](https://www.nvidia.com/en-us/data-center/base-command-manager/)

### Installation

1. Clone this repository (with submodules):
```bash
git clone --recurse-submodules https://gitlab-master.nvidia.com/travisw/bcm-in-nvidia-air.git
cd bcm-in-nvidia-air
```

   If you already cloned without `--recurse-submodules`, initialize the submodule:
```bash
git submodule update --init
```

2. Install uv (fast Python package installer):
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

3. Create a virtual environment:
```bash
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

4. Install Python dependencies using uv:
```bash
# For WSL users with project on /mnt/c/, suppress harmless hardlink warning:
export UV_LINK_MODE=copy

# Install dependencies
uv pip install -e .
```

5. Install expect (required for password configuration fallback on free tier):
```bash
sudo apt install -y expect
```
This is especially important for air.nvidia.com free accounts where cloud-init may not be available.

6. Configure your NVIDIA Air credentials:
```bash
# Copy the example environment file
cp sample-configs/env.example .env

# Edit .env with your actual credentials
# Required fields:
#   AIR_API_TOKEN - Your API token from Air
#   AIR_USERNAME - Your Air account email
#   AIR_API_URL - Air site URL (air.nvidia.com or air-inside.nvidia.com)
#   BCM_PRODUCT_KEY - Your BCM license key
#   BCM_ADMIN_EMAIL - Admin email for BCM
```

**Example `.env` file:**
```bash
AIR_API_TOKEN=your_actual_token_here
AIR_USERNAME=your_email@nvidia.com
AIR_API_URL=https://air.nvidia.com  # or https://air-inside.nvidia.com
BCM_PRODUCT_KEY=123456-789012-345678-901234-567890
BCM_ADMIN_EMAIL=your_email@nvidia.com
```

> **⚠️ License MAC Address:** BCM licenses are bound to the MAC address of the head node's outbound interface. The default topology (`topologies/default.json`) sets a static MAC address to ensure your license works consistently across simulation rebuilds. If you need to use a different MAC (to match an existing license), update the `mac` field on the BCM node's outbound interface in your topology file.

8. Place your BCM ISO file:
```bash
# Create the .iso directory
mkdir -p .iso

# Copy or move your downloaded BCM ISO
cp ~/Downloads/bcm-10.0-ubuntu2404.iso .iso/
cp ~/Downloads/bcm-11.0-ubuntu2404.iso .iso/
```

**ISO Filename Patterns:**

The script auto-detects ISO files based on filename patterns. Supported formats:

| Pattern | Example | Version Detected |
|---------|---------|------------------|
| `bcm-MAJOR.MINOR.PATCH-*.iso` | `bcm-10.30.0-ubuntu2404.iso` | 10.30.0 |
| `bcm-MAJOR.MINOR-*.iso` | `bcm-10.30-ubuntu2404.iso` | 10.30.0 |
| `bcm-MAJOR.MINOR.PATCH.iso` | `bcm-10.30.0.iso` | 10.30.0 |
| `bcmMAJOR.MINOR.PATCH.iso` | `bcm10.30.0.iso` | 10.30.0 |

**If you have multiple ISOs of the same major version** (e.g., both 10.24 and 10.30):
- Rename them to include the full version: `bcm-10.24.03-ubuntu2404.iso`, `bcm-10.30.0-ubuntu2404.iso`
- Use `--bcm-version 10.24.03` or `--bcm-version 10.30.0` to select the specific release
- In non-interactive mode (`-y`), you must specify the exact version if multiple ISOs exist

8. **(Optional)** Verify your setup:
```bash
python scripts/check_setup.py
```

This will check that all prerequisites are met before deployment.

### Deploy BCM

**Quick Configuration Reference:**

| Site | Configuration | Command |
|------|---------------|---------|
| External (default) | Set `AIR_API_URL=https://air.nvidia.com` in `.env` | `python deploy_bcm_air.py` |
| Internal (NVIDIA) | Set `AIR_API_URL=https://air-inside.nvidia.com` in `.env` | `python deploy_bcm_air.py` or use `--internal` flag |

Run the automated deployment script:

```bash
# Deploy to external site (default)
python deploy_bcm_air.py

# Or deploy to internal site using --internal flag
python deploy_bcm_air.py --internal
```

The script will:
1. Prompt you to choose BCM version (10.x or 11.x)
2. Prompt for password (default: `Nvidia1234!`) or your custom password
3. Create the Air simulation with all nodes and network topology
4. Wait for simulation to load and nodes to boot
5. Upload your BCM ISO to the head node via rsync (~10-20 min for 5GB)
6. Execute BCM installation script on head node (~30-45 min)
   - Uploads bcm-ansible-installer submodule to head node
   - Installs Ansible Galaxy collection
   - Runs official BCM installation playbook
7. Configure passwords, DNS, and TFTP

**That's it!** You can work on other things while it installs - the script runs unattended after ISO upload.

### Access Your BCM Environment

After deployment completes, the script automatically:
- ✅ Enables SSH service directly on `bcm-01:eth0`
- ✅ Creates `.ssh/<simulation-name>` config file for easy SSH access
- ✅ Configures password and SSH key authentication

**Easy SSH Access (using generated config):**
```bash
# SSH to BCM head node
ssh -F .ssh/202512001-BCM-Lab air-bcm-01

# Or use the 'bcm' alias
ssh -F .ssh/202512001-BCM-Lab bcm

# Your SSH key from ~/.ssh/id_rsa is automatically used
```

**Manual SSH Access (if needed):**
```bash
# Direct SSH (get host/port from script output)
ssh -p <port> ubuntu@<worker>.air.nvidia.com

# Default password: nvidia (or Nvidia1234! if cloud-init worked)
```

**Automated Password & SSH Key Configuration:**

During deployment, you'll be prompted to:
- Use default password: `Nvidia1234!`
- Or specify your own custom password

The script uses **cloud-init** (preferred method) for configuration:
1. Creates a UserConfig script via Air SDK (`air.user_configs.create()`)
2. Assigns the cloud-init user-data to all Ubuntu nodes
3. Passwords AND SSH keys are automatically set during first boot
4. No interactive prompts or SSH automation needed!

**Setup (one-time):**
```bash
# Copy the example template
cp sample-configs/cloud-init-password.yaml.example cloud-init-password.yaml

# Edit and add your SSH public key
# Replace YOUR_SSH_PUBLIC_KEY_HERE with your actual key from:
#   cat ~/.ssh/id_rsa.pub
# or
#   cat ~/.ssh/id_ed25519.pub
```

**What cloud-init configures:**
- ✅ Sets password for `root` and `ubuntu` users
- ✅ Adds your SSH key to `ubuntu` user
- ✅ Adds your SSH key to `root` user
- ✅ Enables password auth as fallback

**Fallback:** If cloud-init fails, the script automatically falls back to SSH-based configuration using `expect`.

**Files:**
- `sample-configs/cloud-init-password.yaml.example` - Template (in version control)
- `cloud-init-password.yaml` - Your config with SSH key (gitignored)

**Requirements:** `air-sdk` must be installed (`pip install air-sdk`)

**BCM Shell:**
```bash
cmsh
```

**BCM GUI Access:**
1. In NVIDIA Air, use "ADD SERVICE" to expose TCP port 8081 on bcm-01
2. Access BCM web interface:
   - `https://<worker_url>:<port>/userportal`
   - `https://<worker_url>:<port>/base-view`

## Network Topology

The default topology (`topologies/default.json`) creates the following environment:

| Node name        | Interface | IP address        | Function          |
| ---------------- | --------- | ----------------- | ----------------- |
| bcm-01           | eth0      | DHCP (outbound)   | BCM head node     |
| bcm-01           | eth4      | 192.168.200.254   | Management network|
| oob-mgmt-switch  | -         | -                 | OOB switch        |
| leaf-01 to 04    | eth0      | (via oob-switch)  | Cumulus switches  |
| spine-01, 02     | eth0      | (via oob-switch)  | Spine switches    |
| cpu-01 to 05     | eth0      | (PXE boot)        | Compute nodes     |

**Key Network Details:**
- **Outbound Interface**: `bcm-01:eth0` → `outbound` (DHCP, internet access, SSH service)
- **Management Network**: `192.168.200.0/24` via `oob-mgmt-switch`
- **BCM Management IP**: `192.168.200.254` on the interface connected to `oob-mgmt-switch`
- **Static MAC for Licensing**: `48:b0:2d:00:00:00` on `bcm-01:eth0`

## Project Structure

**Main Files:**
- `deploy_bcm_air.py` - Main deployment automation script
- `sample-configs/env.example` - Environment variable template
- `sample-configs/cloud-init-password.yaml.example` - Cloud-init template

**Scripts:**
- `scripts/bcm_install.sh` - BCM installation script (runs on head node)
  - Uses bcm-ansible-installer (uploaded from submodule)
  - Generates cluster credentials and settings
  - Runs official BCM Ansible playbook locally
- `scripts/cumulus-ztp.sh` - Zero-touch provisioning script for Cumulus switches

**Topologies:**
- `topologies/default.json` - Default BCM lab topology (JSON format)
- `topologies/README.md` - Topology design requirements and documentation

**ISO Directory:**
- `.iso/` - Place your BCM ISO files here (gitignored)
  - `bcm-10.30.0-ubuntu2404.iso` - BCM 10.x ISO (example)
  - `bcm-10.24.03-ubuntu2404.iso` - Another BCM 10.x release (example)
  - `bcm-11.0-ubuntu2404.iso` - BCM 11.x ISO (example)
  - See "ISO Filename Patterns" above for supported naming conventions

**Tools:**
**Testing/Debug Scripts:**
- `scripts/check_setup.py` - Environment setup verification
- `scripts/check_sim_state.py` - Debug simulation state
- `scripts/test_sdk_auth.py` - Test Air SDK authentication
- `scripts/test_direct_auth.py` - Test direct API authentication
- `scripts/test_auth.sh` - Shell-based auth test

## Creating Custom Topology Files

Create custom topologies using the NVIDIA Air web UI and export them to JSON format.

### Workflow

1. **Create in NVIDIA Air Web UI**: Use the visual topology editor at air.nvidia.com (or air-inside.nvidia.com)
2. **Export to JSON**: Use the export function to download the topology as JSON
3. **Place in `topologies/` directory**: Save the JSON file in this directory
4. **Deploy**: Run `python deploy_bcm_air.py --topology topologies/your-topology.json`

### Design Requirements

See `topologies/README.md` for full requirements. Key points:

1. **BCM Node Must Use eth0 for "outbound"**: The BCM head node's `eth0` **must** be connected to `"outbound"` for SSH access. This is a NVIDIA Air requirement - the `40-air.yaml` netplan only configures `eth0` for DHCP.
2. **BCM Node Naming**: Node name must start with `bcm` (e.g., `bcm-01`, `bcm-headnode`)
3. **Disable OOB (Recommended)**: Set `"oob": false` to have full control over all interfaces
4. **Management Interface**: Connect another interface to `oob-mgmt-switch` for the 192.168.200.0/24 network

### Example Link to "outbound"

In your JSON topology, ensure the BCM node has `eth0` connected to outbound:

```json
[
    {
        "interface": "eth0",
        "node": "bcm-01",
        "mac": "48:b0:2d:00:00:00"
    },
    "outbound"
]
```

**⚠️ Important**: Using any interface other than `eth0` for outbound will cause hostname and IP assignment failures.

### Static MAC Address for Licensing

BCM licenses are bound to MAC addresses. Keep the MAC address on your BCM node's outbound interface consistent across topology rebuilds to maintain license validity.

### Using Your Custom Topology

```bash
# Deploy with custom topology
python deploy_bcm_air.py --topology topologies/my-topology.json

# With internal Air site
python deploy_bcm_air.py --internal --topology topologies/my-topology.json
```

### Validation

The script validates:
- ✅ BCM node exists (name starts with `bcm`)
- ✅ BCM node has an interface connected to `"outbound"`
- ✅ Topology is in JSON format

## Next Steps: Device Onboarding

After BCM is installed, you'll need to onboard the switches and compute nodes into BCM management. The exact steps depend on your topology.

### 1. Access BCM Shell

SSH to bcm-01 and enter the BCM shell:

```bash
# Using the generated SSH config
ssh -F .ssh/<simulation-name> bcm

# Or direct SSH (use worker/port from deployment output)
ssh -p <port> ubuntu@<worker>.air.nvidia.com

# Enter BCM shell
cmsh
```

### 2. Verify Network Configuration

BCM should already have the management network configured:

```bash
cmsh
network
use internalnet
get gateway
# Should show the management network gateway
```

### 3. Onboard Cumulus Switches (Example)

From the BCM shell (`cmsh`), add switches:

```bash
device
add switch leaf-01
set mac <mac-from-topology>
set disablesnmp yes
set hasclientdaemon yes
ztpsettings 
set enableapi yes
commit
```

Then on the switch console, enable ZTP and reboot:

```bash
sudo ztp -e
sudo reboot
```

### 4. Onboard Compute Nodes (Example)

Add compute nodes for PXE boot management:

```bash
device
add PhysicalNode cpu-01
set mac <mac-from-topology>
commit
```

Reboot the compute nodes in NVIDIA Air to start the PXE boot process.

### 5. Monitor Device Status

Check device status and wait for them to become `UP`:

```bash
device
list
```

**Expected progression:**
- **Switches**: `BOOTING` → `UP` (2-5 minutes)
- **Compute Nodes**: `BOOTING` → `INSTALLING` → `INSTALLER_CALLINGINIT` → `UP` (5-10 minutes)

## Advanced Configuration

### BCM Version Information

Check the installed BCM version:

```bash
cmd -v
```

### Accessing BCM GUI

The BCM web interface runs on port 8081. To access it:

1. In NVIDIA Air, use "ADD SERVICE" to expose TCP port 8081 on bcm-01
2. Access the GUI at:
   - `https://<worker_url>:<tcp_port>/userportal`
   - `https://<worker_url>:<tcp_port>/base-view`

### Installing NVIDIA Air Agent (Optional)

If you want to control the BCM node programmatically via NVIDIA Air SDK:

```bash
ssh root@192.168.200.254

git clone https://github.com/NVIDIA/air_agent.git
cd air_agent/
./install.sh
```

The Air Agent will be installed and enabled as a systemd service, allowing API-based control of the VM.

## Troubleshooting

### BCM Installation Issues

If BCM installation fails, check:
- Network connectivity on bcm-01
- Available disk space: `df -h`
- BCM logs: `/var/log/cmd.log`

### Device Onboarding Issues

If switches or compute nodes don't appear in BCM:
- Verify MAC addresses match the topology
- Check DHCP is disabled on oob-mgmt-server
- Verify network connectivity: `ping 192.168.200.12` (from BCM node)
- Check ZTP logs on switches: `/var/log/syslog`

### Authentication Errors (401/403)

If you get an authentication error:

```
✗ Authentication Failed: 403
Response: {"detail":"Authentication credentials were not provided."}
```

**Common causes:**
1. `AIR_API_TOKEN` environment variable is not set
2. API token is invalid or expired
3. Using wrong token (internal vs external site tokens are different)

**Solutions:**

```bash
# Check if .env file exists and is configured
cat .env

# Verify token is set
python -c "from dotenv import load_dotenv; import os; load_dotenv(); print('Token:', os.getenv('AIR_API_TOKEN', 'NOT SET'))"

# Update your .env file with correct credentials:
# 1. Log in to air.nvidia.com or air-inside.nvidia.com
# 2. Go to Account Settings → API Tokens
# 3. Generate a new token
# 4. Update AIR_API_TOKEN in your .env file
```

**Important:** API tokens for `air.nvidia.com` and `air-inside.nvidia.com` are **different**. Make sure you're using the token from the correct site.

### Connection to Internal Air Site Fails

If you get a DNS resolution error when using `--internal` or `air-inside.nvidia.com`:

```
Failed to resolve 'air-inside.nvidia.com'
```

**This is expected** - the internal Air site requires:
- Connection to NVIDIA internal network, OR
- Active NVIDIA VPN connection

**Solutions:**
1. Connect to NVIDIA VPN and try again
2. Use the external site instead: `python deploy_bcm_air.py` (remove `--internal` flag)
3. Verify you can access https://air-inside.nvidia.com in your browser

### SSH Access Issues

**SSH Connection to BCM Node:**

The SSH service is created directly on `bcm-01:eth0`. Use the generated SSH config:

```bash
# Use the SSH config created during deployment
ssh -F .ssh/<simulation-name> air-bcm-01

# Or use the 'bcm' alias
ssh -F .ssh/<simulation-name> bcm
```

**Default Passwords:**

| Scenario | Username | Password |
|----------|----------|----------|
| Cloud-init worked (air-inside) | ubuntu/root | Your configured password (default: `Nvidia1234!`) |
| Cloud-init unavailable (air.nvidia.com free tier) | ubuntu | `nvidia` |

**Free Tier Limitations:**

On air.nvidia.com free accounts, cloud-init may not be available. The script will:
1. Attempt cloud-init configuration
2. Fall back to SSH-based configuration using `expect`
3. If both fail, you'll need to use the default password `nvidia`

**SSH Key Permission Errors in WSL:**

If you get "UNPROTECTED PRIVATE KEY FILE" error:
```bash
# Create new key on WSL filesystem (not Windows /mnt/c/)
ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa_wsl

# Update SSH_PRIVATE_KEY and SSH_PUBLIC_KEY in .env to use the new key
```

**Password Change Prompt:**

If you see "You must change your password now" when connecting:
- This happens on first login with default Ubuntu images
- The script tries to handle this automatically with `expect`
- If prompted, change to your desired password

### Ansible Playbook Failures

If Ansible fails during deployment:
- Check logs: `ssh -F .ssh/<sim-name> bcm 'cat /home/ubuntu/ansible_bcm_install.log'`
- Verify Ansible collections: `ansible-galaxy collection list`
- Check ISO mounted: `ssh -F .ssh/<sim-name> bcm 'ls /mnt/dvd'`

### Dependency Management Issues

This project uses `uv` for fast Python dependency management. Common uv commands:

```bash
# Install/sync dependencies
uv pip install -e .

# Add a new dependency
uv pip install <package-name>

# Update all dependencies
uv pip install --upgrade -e .

# Clear uv cache
uv cache clean

# Check uv version
uv --version
```

**WSL Users:** If you see a warning about "Failed to hardlink files", this is expected when your project is on `/mnt/c/` (Windows filesystem) and is harmless. To suppress the warning:
```bash
export UV_LINK_MODE=copy
```

If you prefer using traditional pip, you can still use `requirements.txt`:
```bash
pip install -r requirements.txt
```

## Additional Resources

- [NVIDIA Air Documentation](https://docs.nvidia.com/networking-ethernet-software/nvidia-air/)
- [Bright Cluster Manager Documentation](https://www.brightcomputing.com/documentation)
- [Ansible Galaxy - BCM 10.x Collection](https://galaxy.ansible.com/ui/repo/published/brightcomputing/bcm100/)
- [Ansible Galaxy - BCM 11.x Collection](https://galaxy.ansible.com/ui/repo/published/brightcomputing/bcm110/)

## Script Reference

**deploy_bcm_air.py Options:**

```bash
# Show help
python deploy_bcm_air.py --help

# Non-interactive mode (accept all defaults)
python deploy_bcm_air.py -y

# Specify BCM version (major version)
python deploy_bcm_air.py --bcm-version 10
python deploy_bcm_air.py --bcm-version 11

# Specify exact BCM release (when multiple ISOs available)
python deploy_bcm_air.py --bcm-version 10.30.0
python deploy_bcm_air.py --bcm-version 10.24.03

# Deploy to internal NVIDIA Air site
python deploy_bcm_air.py --internal

# Use custom topology file
python deploy_bcm_air.py --topology topologies/my-topology.json

# Resume from last checkpoint
python deploy_bcm_air.py --resume

# Create simulation only (skip BCM installation)
python deploy_bcm_air.py --skip-ansible
```

**Configuration (.env file):**

All configuration is managed through a `.env` file in the project root. Copy `sample-configs/env.example` to `.env` and configure:

- `AIR_API_TOKEN` - Your NVIDIA Air API authentication token (required)
- `AIR_USERNAME` - Your Air account email address (required)
- `AIR_API_URL` - NVIDIA Air API base URL (required)
  - External: `https://air.nvidia.com`
  - Internal: `https://air-inside.nvidia.com`
- `UV_LINK_MODE` - Set to `copy` to suppress hardlink warnings in WSL (optional)

**Note:** Command-line flags (`--internal`, `--api-url`) will override `.env` settings.

## Repository Structure

```
bcm-in-nvidia-air/
├── deploy_bcm_air.py              # Main automation script (START HERE!)
├── README.md                      # This file
├── .env                           # Your environment config (create from sample-configs/env.example)
├── cloud-init-password.yaml       # Your config with SSH key (auto-generated)
│
├── bcm-ansible-installer/         # Git submodule: Ansible scaffolding for BCM
│   ├── playbook.yml               # Main Ansible playbook
│   ├── inventory/hosts            # Ansible inventory
│   ├── ansible.cfg                # Ansible configuration
│   └── requirements-control-node.txt  # Python dependencies for Ansible
│
├── sample-configs/                # Example configuration templates
│   ├── env.example                # Example environment configuration
│   └── cloud-init-password.yaml.example  # Cloud-init template
│
├── .iso/                          # BCM ISO files (gitignored)
│   ├── bcm-10.30.0-ubuntu2404.iso # BCM 10.x ISO (example)
│   └── bcm-11.0-ubuntu2404.iso    # BCM 11.x ISO (example)
│
├── .ssh/                          # Generated SSH configs (gitignored)
│   └── <simulation-name>          # SSH config for each simulation
│
├── .logs/                         # Progress tracking (gitignored)
│   └── progress.json              # Deployment checkpoint for --resume
│
├── scripts/                       # All scripts (see scripts/README.md)
│   ├── README.md                  # Script documentation
│   ├── bcm_install.sh             # BCM installation script (runs on head node)
│   ├── check_setup.py             # Setup verification helper
│   ├── topology_validation.py     # Validate topology files
│   └── ...                        # Testing/debug scripts
│
├── topologies/                    # Network topology files (JSON format)
│   ├── README.md                  # Design requirements documentation
│   └── default.json               # Default BCM lab topology
│
├── pyproject.toml                 # Project metadata and dependencies (uv)
└── requirements.txt               # Python dependencies (pip fallback)
```

## How It Works

The deployment uses a two-phase approach:

**Phase 1: Simulation Setup (your machine)**
1. Creates NVIDIA Air simulation via API
2. Attempts cloud-init for password/SSH key configuration
   - Falls back to SSH-based configuration if cloud-init unavailable (free tier)
3. Waits for simulation to load
4. Enables SSH service on `bcm-01:eth0`
5. Uploads BCM ISO via rsync (reliable, resumable)
6. Uploads installation script with your credentials

**Phase 2: BCM Installation (on head node)**
1. Uses bcm-ansible-installer (uploaded from submodule during Phase 1)
2. Installs Ansible Galaxy collection (`brightcomputing.installer100` or `installer110`)
3. Generates cluster credentials and network settings from topology
4. Runs official BCM Ansible playbook locally
5. Configures DNS, TFTP, and passwords

**bcm-ansible-installer Submodule:**
The `bcm-ansible-installer/` submodule provides minimal scaffolding for the Ansible installation:
- `playbook.yml` - Calls the Galaxy collection role
- `inventory/hosts` - Defines head_node target
- `requirements-control-node.txt` - Python dependencies
- `ansible.cfg` - Ansible configuration

This uses the official Bright Computing Ansible Galaxy collections for actual BCM installation.

---

**Note:** This automated deployment replaces the previous manual process of creating, modifying, and uploading custom BCM images. The new approach uses stock Ubuntu 24.04 images available in NVIDIA Air, making deployment faster and more maintainable.
