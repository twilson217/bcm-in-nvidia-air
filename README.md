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
- [bcm-ansible-installer](https://github.com/berkink-nvidia-com/bcm-ansible-installer) - GitHub repo cloned during installation
- [brightcomputing.installer100](https://galaxy.ansible.com/ui/repo/published/brightcomputing/bcm100/) - Ansible Galaxy collection for BCM 10.x
- [brightcomputing.installer110](https://galaxy.ansible.com/ui/repo/published/brightcomputing/bcm110/) - Ansible Galaxy collection for BCM 11.x

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
5. **BCM Product Key** - Your license key for BCM installation

### Installation

1. Clone this repository:
```bash
git clone https://gitlab-master.nvidia.com/travisw/bcm-in-nvidia-air.git
cd bcm-in-nvidia-air
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

5. Install expect (used for fallback password configuration):
```bash
sudo apt install -y expect
```

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
# For BCM 10.x:
cp ~/Downloads/bcm-10.0-ubuntu2404.iso .iso/

# For BCM 11.x:
cp ~/Downloads/bcm-11.0-ubuntu2404.iso .iso/
```

The script will automatically detect the ISO matching your selected BCM version.

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
   - Clones [bcm-ansible-installer](https://github.com/berkink-nvidia-com/bcm-ansible-installer)
   - Installs Ansible Galaxy collection
   - Runs official BCM installation playbook
7. Configure passwords, DNS, and TFTP

**That's it!** You can work on other things while it installs - the script runs unattended after ISO upload.

### Access Your BCM Environment

After deployment completes, the script automatically:
- ✅ Enables SSH service for the simulation
- ✅ Creates `.ssh/config` file for easy SSH access
- ✅ Configures ProxyJump through oob-mgmt-server

**Easy SSH Access (using generated config):**
```bash
# SSH to OOB management server
ssh -F .ssh/config air-oob

# SSH to BCM head node (via ProxyJump)
ssh -F .ssh/config air-bcm-01

# Your SSH key from ~/.ssh/id_rsa is automatically used
```

**Manual SSH Access (if needed):**
```bash
# Via Air console (click "SSH" in Air UI)
# Or via ProxyJump manually:
ssh -J ubuntu@workerNN.air-inside.nvidia.com:PORT root@bcm-01
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

The deployment creates the following environment:

| Node name        | IP address        | MAC address       | Function          |
| ---------------- | ----------------- | ----------------- | ----------------- |
| bcm-01           | 192.168.200.254   | (auto)            | BCM head node     |
| oob-mgmt-server  | 192.168.200.1     | (auto)            | OOB management    |
| oob-mgmt-switch  | 192.168.200.251   | (auto)            | OOB switch        |
| leaf01           | 192.168.200.12    | 44:38:39:22:AA:02 | Cumulus switch    |
| leaf02           | 192.168.200.13    | 44:38:39:22:AA:03 | Cumulus switch    |
| compute0         | 192.168.200.14    | 44:38:39:22:AA:04 | PXE boot server   |
| compute1         | 192.168.200.15    | 44:38:39:22:AA:05 | PXE boot server   |
| compute2         | 192.168.200.16    | 44:38:39:22:AA:06 | PXE boot server   |
| ubuntu0          | 192.168.200.10    | 44:38:39:22:AA:01 | Ubuntu server     |

Network: `192.168.200.0/24` (internal OOB management network)

## Project Structure

**Main Files:**
- `deploy_bcm_air.py` - Main deployment automation script
- `sample-configs/env.example` - Environment variable template
- `sample-configs/cloud-init-password.yaml.example` - Cloud-init template

**Scripts:**
- `scripts/bcm_install.sh` - BCM installation script (runs on head node)
  - Clones bcm-ansible-installer from GitHub
  - Generates cluster credentials and settings
  - Runs official BCM Ansible playbook locally
- `scripts/cumulus-ztp.sh` - Zero-touch provisioning script for Cumulus switches

**Topologies:**
- `topologies/default.json` - Default BCM lab topology (JSON format)
- `topologies/topology-design.md` - Topology design requirements

**ISO Directory:**
- `.iso/` - Place your BCM ISO files here (gitignored)
  - `bcm-10.0-ubuntu2404.iso` - BCM 10.x ISO
  - `bcm-11.0-ubuntu2404.iso` - BCM 11.x ISO

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

See `topologies/topology-design.md` for full requirements. Key points:

1. **BCM Node Must Connect to "outbound"**: The BCM head node must have an interface connected to `"outbound"` for SSH access
2. **BCM Node Naming**: Node name must start with `bcm` (e.g., `bcm-01`, `bcm-headnode`)
3. **Disable OOB (Recommended)**: Set `"oob": false` to have full control over all interfaces

### Example Link to "outbound"

In your JSON topology, ensure the BCM node has a link like:

```json
[
    {
        "interface": "eth4",
        "node": "bcm-01",
        "mac": "48:b0:2d:a4:dc:c1"
    },
    "outbound"
]
```

The script auto-detects this interface and creates the SSH service on it.

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

After BCM is installed, you'll need to onboard the switches and compute nodes into BCM management.

### 1. Disable DHCP on oob-mgmt-server

First, disable the default DHCP server so BCM can manage DHCP:

```bash
# Connect to oob-mgmt-server and run:
sudo systemctl disable isc-dhcp-server
sudo service isc-dhcp-server stop
sudo service isc-dhcp-server status
```

### 2. Configure BCM Network Gateway

SSH to bcm-01 and configure the network gateway:

```bash
ssh root@192.168.200.254
# Password: Nvidia1234! (or your custom password if specified)

# Enter BCM shell
cmsh

# Configure gateway (automated by deployment script, but verify)
network
use internalnet
set gateway 192.168.200.1
commit
```

### 3. Onboard Cumulus Switches

From the BCM shell (`cmsh`), add the Cumulus switches:

**Add leaf01:**
```
device
list
device add switch leaf01 192.168.200.12
set mac 44:38:39:22:AA:02
set disablesnmp yes
set hasclientdaemon yes
ztpsettings 
set enableapi yes
commit
```

Then on leaf01 console, enable ZTP and reboot:

```bash
sudo ztp -e
sudo reboot
```

**Add leaf02:**

```bash
device
list
device add switch leaf02 192.168.200.13
set mac 44:38:39:22:AA:03
set disablesnmp yes
set hasclientdaemon yes
ztpsettings 
set enableapi yes
commit
```

Then on leaf02 console:

```bash
sudo ztp -e
sudo reboot
```

### 4. Onboard Compute Nodes

Add compute nodes to BCM for PXE boot management:

**Add compute0:**

```bash
device
list
device add PhysicalNode compute0 192.168.200.14
set mac 44:38:39:22:AA:04
commit
```

**Add compute1:**

```bash
device add PhysicalNode compute1 192.168.200.15
set mac 44:38:39:22:AA:05
commit
```

**Add compute2:**

```bash
device add PhysicalNode compute2 192.168.200.16
set mac 44:38:39:22:AA:06
commit
```

Reboot the compute nodes in NVIDIA Air so PXE boot process starts.

### 5. Monitor Device Status

From BCM `cmsh` command line, check the status of devices and wait for them to become `UP`:

```bash
device
list
```

**Expected device progression:**

**Switches:** You'll see status change from `BOOTING` → `UP` as ZTP completes and cm-lite-daemon registers (takes 2-5 minutes).

**Compute Nodes:** Status will progress through `BOOTING` → `INSTALLING` → `INSTALLER_CALLINGINIT` → `UP` (takes 5-10 minutes).

Example output when all devices are online:

```
Type          Hostname         MAC                IP              Status
------------- ---------------- ------------------ --------------- ----------
HeadNode      bcm-nv-air       48:B0:2D:11:FE:92  192.168.200.254 [ UP ]
PhysicalNode  compute0         44:38:39:22:AA:04  192.168.200.14  [ UP ]
PhysicalNode  compute1         44:38:39:22:AA:05  192.168.200.15  [ UP ]
PhysicalNode  compute2         44:38:39:22:AA:06  192.168.200.16  [ UP ]
Switch        leaf01           44:38:39:22:AA:02  192.168.200.12  [ UP ]
Switch        leaf02           44:38:39:22:AA:03  192.168.200.13  [ UP ]
```

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

**SSH Key Only Works on oob-mgmt-server:**

Air automatically adds your SSH key to `oob-mgmt-server`, but not to other nodes. To enable password-less SSH to all nodes:

**Option 1: Copy SSH key to nodes (recommended)**
```bash
# From oob-mgmt-server, copy key to BCM node
ssh -F .ssh/config air-oob
ssh-copy-id root@bcm-01
# Enter password when prompted (default: Nvidia1234!)
```

**Option 2: Use password authentication**
```bash
# Install sshpass if needed
sudo apt install sshpass

# SSH with password
sshpass -p 'Nvidia1234!' ssh -F .ssh/config air-bcm-01
```

**SSH Key Permission Errors in WSL:**

If you get "UNPROTECTED PRIVATE KEY FILE" error:
```bash
# Create new key on WSL filesystem (not Windows /mnt/c/)
ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa2

# Upload new key to Air (copy contents of ~/.ssh/id_rsa2.pub to Air profile)

# Update config to use new key
# The generated .ssh/config automatically uses ~/.ssh/id_rsa
```

**Password Change Prompt:**

If you see "You must change your password now" when connecting:
- This is the default Ubuntu behavior
- The script sets the default password during deployment
- Use the password you specified (or Nvidia1234! if using default)

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

# Deploy to internal NVIDIA Air site
python deploy_bcm_air.py --internal

# Deploy with custom API URL
python deploy_bcm_air.py --api-url https://custom-air.example.com/api/v2

# Deploy with custom name
python deploy_bcm_air.py --name my-bcm-cluster

# Use custom topology file
python deploy_bcm_air.py --dot-file custom-topology.dot

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
├── cloud-init-password.yaml       # Your config with SSH key (auto-generated or copy from sample-configs/)
│
├── sample-configs/                # Example configuration templates
│   ├── env.example                # Example environment configuration
│   └── cloud-init-password.yaml.example  # Cloud-init template
│
├── .iso/                          # BCM ISO files (gitignored)
│   └── bcm-10.0-ubuntu2404.iso    # Place your BCM ISO here
│
├── scripts/                       # All scripts (installation, ZTP, testing)
│   ├── bcm_install.sh             # BCM installation script (runs on head node)
│   ├── cumulus-ztp.sh             # Cumulus switch ZTP script
│   ├── check_setup.py             # Setup verification helper
│   ├── check_sim_state.py         # Debug simulation state
│   ├── test_sdk_auth.py           # SDK authentication test
│   ├── test_direct_auth.py        # Direct API authentication test
│   └── test_auth.sh               # Shell-based auth test
│
├── topologies/                    # Network topology files (JSON format)
│   ├── default.json               # Default BCM lab topology
│   ├── test-bcm.json              # Minimal test topology
│   └── topology-design.md         # Design requirements documentation
│
│
├── pyproject.toml                 # Project metadata and dependencies (uv)
└── requirements.txt               # Python dependencies (pip fallback)
```

## How It Works

The deployment uses a two-phase approach:

**Phase 1: Simulation Setup (your machine)**
1. Creates NVIDIA Air simulation via API
2. Applies cloud-init for password/SSH key configuration
3. Waits for simulation to load
4. Uploads BCM ISO via rsync (reliable, resumable)
5. Uploads installation script with your credentials

**Phase 2: BCM Installation (on head node)**
1. Mounts ISO as installation source
2. Clones [bcm-ansible-installer](https://github.com/berkink-nvidia-com/bcm-ansible-installer)
3. Installs Ansible Galaxy collection (`brightcomputing.installer100` or `installer110`)
4. Generates cluster credentials and network settings
5. Runs official BCM Ansible playbook locally
6. Configures DNS, TFTP, and passwords

**GitHub Dependency:**
The installation script clones `https://github.com/berkink-nvidia-com/bcm-ansible-installer.git` which provides:
- Wrapper playbook for BCM installation
- Inventory templates for head node setup
- Post-installation DNS configuration tasks

This repo in turn uses the official Bright Computing Ansible Galaxy collections.

---

**Note:** This automated deployment replaces the previous manual process of creating, modifying, and uploading custom BCM images. The new approach uses stock Ubuntu 24.04 images available in NVIDIA Air, making deployment faster and more maintainable.
