# Topology Design Requirements

This document describes the design constraints for topology files used with the BCM deployment automation.

## Creating Custom Topologies

The recommended workflow for custom topologies:

1. **Create in NVIDIA Air Web UI**: Use the visual topology editor at air.nvidia.com (or air-inside.nvidia.com for internal use)
2. **Export to JSON**: Use the export function to download the topology as JSON
3. **Place in `topologies/` directory**: Save the JSON file in this directory
4. **Deploy**: Run `python deploy_bcm_air.py --topology topologies/your-topology.json`

## Required Design Constraints

### 1. BCM Node Must Use eth0 for "outbound"

**Requirement**: The BCM head node's **eth0** must be connected to `"outbound"`.

**Why**: This interface is used for:
- SSH service for external access (allows deployment script to connect)
- BCM's "external interface" for internet access (DHCP)

**⚠️ Critical**: NVIDIA Air's infrastructure has special handling for `eth0`:
- The `40-air.yaml` netplan configuration specifically configures `eth0` for DHCP
- Using a different interface (e.g., eth3, eth4) for outbound results in:
  - Hostname not being set properly (stays as "ubuntu")
  - Interface not getting an IP address automatically
  - SSH service failing to work

**Example** (from `default.json`):
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

**Example** (from `default.json`):
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

**Default topology (`default.json`) mapping:**
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

**Example**:
```json
{
    "content": {
        "nodes": {
            "bcm-01": {
                "oob": false
            }
        },
        "oob": false
    }
}
```

### 6. PXE Boot Nodes

**Naming**: PXE boot nodes (compute nodes) should follow a pattern like `cpu-01`, `compute-01`, `node-01`, etc.

**Configuration**: 
- `"os": "pxe"` - Boot from network
- `"boot": "network"` - Enable PXE boot

**Cloud-init**: PXE boot nodes are automatically skipped during cloud-init configuration since they boot from BCM.

## Optional Features

### Static MAC Address for Licensing

**Purpose**: BCM licenses are bound to MAC addresses. Using a static MAC on the BCM node ensures license consistency across simulation rebuilds.

**Example**:
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

**Note**: Keep `48:b0:2d:00:00:00` on `eth0` in all your topologies to maintain license consistency.

## Validation

The deployment script validates and detects:
- ✅ BCM node exists (name starts with `bcm`)
- ✅ BCM node has an interface connected to `"outbound"` → external interface
- ✅ BCM node has an interface connected to `"oob-mgmt-switch"` → management interface
- ✅ Topology is in JSON format

If no `oob-mgmt-switch` connection is found, the script defaults to `eth0` for the management interface.

## Example Topologies

| File | Description |
|------|-------------|
| `default.json` | Full lab with leaf switches, compute nodes, and OOB management |
| `test-bcm.json` | Minimal test topology for development |
