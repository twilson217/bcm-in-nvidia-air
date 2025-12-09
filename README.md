<!-- AIR:tour -->

# Automated BCM Deployment on NVIDIA Air

This repository provides automated deployment of Bright Cluster Manager (BCM) on NVIDIA Air using stock Rocky Linux 9 images and Ansible Galaxy playbooks. No custom image creation or manual configuration required!

## Overview

This solution automates the complete BCM deployment process:
- Creates NVIDIA Air simulation from topology definition
- Deploys BCM 10.x or 11.x (user choice) via Ansible
- Configures network interfaces and storage automatically
- Sets up basic BCM configuration (DHCP gateway)

**Key Benefits:**
- No custom image creation or upload required
- Uses stock Rocky Linux 9 images available in Air
- Choose BCM 10.x or 11.x at deployment time
- Fully automated via NVIDIA Air APIs
- Complete deployment in ~15-20 minutes

## Quick Start

### Prerequisites

1. NVIDIA Air account with API access
   - External site: [air.nvidia.com](https://air.nvidia.com) - publicly accessible
   - Internal site (NVIDIA employees): [air-inside.nvidia.com](https://air-inside.nvidia.com) - **requires NVIDIA VPN or internal network**
2. Python 3.8+ installed locally
3. NVIDIA Air API token (generate from your Air account settings)

### Installation

1. Clone this repository:
```bash
git clone <repository-url>
cd bcm_usecases
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

5. Install system Ansible (required for ansible-galaxy):
```bash
sudo apt install -y software-properties-common
sudo add-apt-repository --yes --update ppa:ansible/ansible
sudo apt install -y ansible
```

6. Install Ansible collections:
```bash
ansible-galaxy collection install -r ansible-requirements.yml
```

7. Configure your NVIDIA Air credentials:
```bash
# Copy the example environment file
cp env.example .env

# Edit .env with your actual credentials
# Required fields:
#   AIR_API_TOKEN - Your API token from Air
#   AIR_USERNAME - Your Air account email
#   AIR_API_URL - Air site URL (air.nvidia.com or air-inside.nvidia.com)
```

**Example `.env` file:**
```bash
AIR_API_TOKEN=your_actual_token_here
AIR_USERNAME=your_email@nvidia.com
AIR_API_URL=https://air.nvidia.com  # or https://air-inside.nvidia.com
```

8. **(Optional)** Verify your setup:
```bash
python tools/check_setup.py
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
2. Create the Air simulation with all nodes and network topology
3. Wait for nodes to boot and become ready
4. Install BCM via Ansible (takes 10-15 minutes)
5. Configure basic network settings

**That's it!** Your BCM environment will be ready to use.

### Access Your BCM Environment

After deployment completes:

**SSH Access:**
```bash
ssh root@192.168.200.254
Password: 3tango
```

**BCM Shell:**
```bash
cmsh
```

**BCM GUI Access:**
1. In NVIDIA Air, use "ADD SERVICE" to expose TCP port 8081 on bcm-headnode0
2. Access BCM web interface:
   - `https://<worker_url>:<port>/userportal`
   - `https://<worker_url>:<port>/base-view`

## Network Topology

The deployment creates the following environment:

| Node name        | IP address        | MAC address       | Function          |
| ---------------- | ----------------- | ----------------- | ----------------- |
| bcm-headnode0    | 192.168.200.254   | (auto)            | BCM head node     |
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
- `env.example` - Environment variable template

**Ansible:**
- `ansible/install_bcm.yml` - Ansible playbook for BCM installation
- `ansible/cumulus-ztp.sh` - Zero-touch provisioning script for Cumulus switches

**Topologies:**
- `topologies/test-bcm.dot` - Network topology definition

**Tools:**
- `tools/check_setup.py` - Environment setup verification
- `tools/test_sdk_auth.py` - Test Air SDK authentication
- `tools/test_direct_auth.py` - Test direct API authentication

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

SSH to bcm-headnode0 and configure the network gateway:

```bash
ssh root@192.168.200.254
# Password: 3tango

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

1. In NVIDIA Air, use "ADD SERVICE" to expose TCP port 8081 on bcm-headnode0
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
- Network connectivity on bcm-headnode0
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

### Ansible Playbook Failures

If Ansible fails during deployment:
- Verify API token is valid: `echo $AIR_API_TOKEN`
- Check Ansible collections installed: `ansible-galaxy collection list`
- Run with verbose output: `ansible-playbook -vvv ansible/install_bcm.yml`

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

All configuration is managed through a `.env` file in the project root. Copy `env.example` to `.env` and configure:

- `AIR_API_TOKEN` - Your NVIDIA Air API authentication token (required)
- `AIR_USERNAME` - Your Air account email address (required)
- `AIR_API_URL` - NVIDIA Air API base URL (required)
  - External: `https://air.nvidia.com`
  - Internal: `https://air-inside.nvidia.com`
- `UV_LINK_MODE` - Set to `copy` to suppress hardlink warnings in WSL (optional)

**Note:** Command-line flags (`--internal`, `--api-url`) will override `.env` settings.

## Repository Structure

```
bcm_usecases/
├── deploy_bcm_air.py              # Main automation script (START HERE!)
├── README.md                      # This file
├── env.example                    # Example environment configuration
├── .env                           # Your environment config (create from env.example)
│
├── ansible/                       # Ansible playbooks and scripts
│   ├── install_bcm.yml            # Ansible playbook for BCM installation
│   └── cumulus-ztp.sh             # Cumulus switch ZTP script
│
├── topologies/                    # Network topology templates
│   └── test-bcm.dot               # Default BCM lab topology
│
├── tools/                         # Testing and troubleshooting utilities
│   ├── check_setup.py             # Setup verification helper
│   ├── test_sdk_auth.py           # SDK authentication test
│   ├── test_direct_auth.py        # Direct API authentication test
│   └── test_auth.sh               # Shell-based auth test
│
├── pyproject.toml                 # Project metadata and dependencies (uv)
├── requirements.txt               # Python dependencies (pip fallback)
└── ansible-requirements.yml       # Ansible Galaxy collections
```

---

**Note:** This automated deployment replaces the previous manual process of creating, modifying, and uploading custom BCM images. The new approach uses stock Rocky Linux 9 images available in NVIDIA Air, making deployment faster and more maintainable.
