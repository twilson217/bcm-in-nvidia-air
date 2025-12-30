# Topology Design Requirements

This document describes the design constraints for topologies used with the BCM deployment automation.

## Directory Structure

Each topology is contained in its own directory:

```
topologies/
├── README.md                    # This file
├── default/                     # Default topology
│   ├── topology.json            # NVIDIA Air topology definition
│   ├── features.yaml            # Post-install feature configuration
│   ├── scripts/                 # ZTP and provisioning scripts
│   │   └── cumulus-ztp.sh       # Switch ZTP script
│   └── bcm-config/              # BCM post-install configurations
│       ├── networks.cmsh        # Define networks
│       ├── interfaces.cmsh      # Bond configuration
│       ├── nodes.cmsh           # Add compute nodes
│       ├── switches.cmsh        # Add switches, configure ZTP
│       └── cm-wlm-setup.conf    # Workload manager config
└── your-custom-topology/        # Your custom topology
    ├── topology.json
    ├── features.yaml
    └── ...
```

## Creating Custom Topologies

### Step 1: Create Topology in NVIDIA Air

1. **Create in NVIDIA Air Web UI**: Use the visual topology editor at air.nvidia.com
2. **Export to JSON**: Use the export function to download the topology as JSON
3. **Create directory**: Create a new directory under `topologies/` for your topology
4. **Save as `topology.json`**: Place the exported JSON in your topology directory

### Step 2: Configure Features (Optional)

Create a `features.yaml` file to enable post-install configurations:

```yaml
# features.yaml - Controls what gets configured after BCM installation
bcm_networks:
  enabled: true
  config_file: bcm-config/networks.cmsh

bcm_interfaces:
  enabled: false
  config_file: bcm-config/interfaces.cmsh

bcm_nodes:
  enabled: false
  config_file: bcm-config/nodes.cmsh

bcm_switches:
  enabled: false
  config_file: bcm-config/switches.cmsh
  ztp_script: scripts/cumulus-ztp.sh

workload_manager:
  enabled: false
  type: slurm  # or kubernetes
  config_file: bcm-config/cm-wlm-setup.conf
```

### Step 3: Deploy

```bash
python deploy_bcm_air.py --topology topologies/your-custom-topology
```

## Required Design Constraints

### 1. BCM Node Must Use eth0 for "outbound"

**Requirement**: The BCM head node's **eth0** must be connected to `"outbound"`.

**Why**: This interface is used for:
- SSH service for external access (allows deployment script to connect)
- BCM's "external interface" for internet access (DHCP)

**Critical**: NVIDIA Air's infrastructure has special handling for `eth0`:
- The `40-air.yaml` netplan configuration specifically configures `eth0` for DHCP
- Using a different interface (e.g., eth3, eth4) for outbound results in:
  - Hostname not being set properly (stays as "ubuntu")
  - Interface not getting an IP address automatically
  - SSH service failing to work

**Example** (from `default/topology.json`):
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

**Recommendation**: Keep the licensing MAC (`48:b0:2d:00:00:00`) on eth0 to maintain license consistency.

### 2. BCM Node Should Connect to "oob-mgmt-switch"

**Requirement**: For BCM to manage compute nodes, the BCM head node should have an interface connected to `oob-mgmt-switch`.

**Why**: This interface becomes BCM's **management interface** (192.168.200.254/24) - the internal network for:
- DHCP/PXE boot services for compute nodes
- Node management and provisioning
- Internal cluster communication

**Example** (from `default/topology.json`):
```json
[
    {
        "interface": "eth4",
        "node": "bcm-01"
    },
    {
        "interface": "swp0",
        "node": "oob-mgmt-switch"
    }
]
```

The script automatically detects this interface and configures it with **192.168.200.254/24**.

### 3. Interface Mapping Summary

| Connection | BCM Role | IP Configuration |
|------------|----------|------------------|
| BCM → `outbound` | External interface | DHCP (internet access) |
| BCM → `oob-mgmt-switch` | Management interface | 192.168.200.254/24 (internal network) |
| BCM → leaf switches | Data plane | Configured later by BCM |

**Default topology mapping:**
- `eth0` → outbound (external, DHCP) - **must be eth0**
- `eth4` → oob-mgmt-switch (management, 192.168.200.254)
- `eth1`, `eth2` → leaf switches (data plane)

### 4. BCM Node Naming Convention

**Requirement**: The BCM head node must have a name starting with `bcm` (case-insensitive).

**Valid names**: `bcm-01`, `bcm-headnode`, `bcm01`, `BCM-primary`, `bcm_head_01`

**Why**: The deployment script auto-detects the BCM node by name pattern to configure cloud-init, SSH, and run the installation.

### 5. OOB Management (Recommended: Disabled)

**Recommendation**: Disable automatic OOB management (`"oob": false`) in the topology.

**Why**: When OOB is enabled, NVIDIA Air reserves `eth0` on all nodes for management, which can conflict with BCM's network configuration. Disabling OOB gives you full control over all interfaces.

### 6. PXE Boot Nodes

**Naming**: PXE boot nodes (compute nodes) should follow a pattern like `cpu-01`, `compute-01`, `node-01`, etc.

**Configuration**: 
- `"os": "pxe"` - Boot from network
- `"boot": "network"` - Enable PXE boot

**Cloud-init**: PXE boot nodes are automatically skipped during cloud-init configuration since they boot from BCM.

## Post-Install Features

The `features.yaml` file controls optional configurations that run after BCM installation:

### bcm_networks
Define additional networks beyond the default internalnet (e.g., ipminet0, dgxnet).

### bcm_interfaces
Configure bonded interfaces for BCM head node and compute node images.

### bcm_nodes
Add compute nodes to BCM's inventory.

### bcm_switches
Add switches to BCM and configure ZTP provisioning. The ZTP script is automatically uploaded to the BCM HTTP directory.

### workload_manager
Configure Slurm or Kubernetes using cm-wlm-setup.

## Validation

The deployment script validates and detects:
- ✅ BCM node exists (name starts with `bcm`)
- ✅ BCM node has an interface connected to `"outbound"` → external interface
- ✅ BCM node has an interface connected to `"oob-mgmt-switch"` → management interface
- ✅ Topology is in JSON format

If no `oob-mgmt-switch` connection is found, the script defaults to `eth0` for the management interface.

## Example Topologies

| Directory | Description |
|-----------|-------------|
| `default/` | Full lab with leaf switches, compute nodes, and OOB management |
| `test-bcm.json` | Legacy minimal test topology (JSON file, no features) |

## Backward Compatibility

Legacy JSON file topologies (like `test-bcm.json`) are still supported. Simply pass the path to the JSON file:

```bash
python deploy_bcm_air.py --topology topologies/test-bcm.json
```

When using legacy JSON files, post-install features are not available.
