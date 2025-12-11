# Topology Design Requirements

This document describes the design constraints for topology files used with the BCM deployment automation.

## Creating Custom Topologies

The recommended workflow for custom topologies:

1. **Create in NVIDIA Air Web UI**: Use the visual topology editor at air.nvidia.com (or air-inside.nvidia.com for internal use)
2. **Export to JSON**: Use the export function to download the topology as JSON
3. **Place in `topologies/` directory**: Save the JSON file in this directory
4. **Deploy**: Run `python deploy_bcm_air.py --topology topologies/your-topology.json`

## Required Design Constraints

### 1. BCM Node Must Connect to "outbound"

**Requirement**: The BCM head node must have exactly one interface connected to `"outbound"`.

**Why**: This interface is used to create an SSH service for external access. Without it, the deployment script cannot establish SSH connectivity to install BCM.

**Example** (from `default.json`):
```json
{
    "links": [
        [
            {
                "interface": "eth4",
                "node": "bcm-01",
                "mac": "48:b0:2d:a4:dc:c1"
            },
            "outbound"
        ]
    ]
}
```

The script will automatically detect which interface connects to `"outbound"` and create the SSH service on that interface.

### 2. BCM Node Naming Convention

**Requirement**: The BCM head node must have a name starting with `bcm` (case-insensitive).

**Valid names**: `bcm-01`, `bcm-headnode`, `bcm01`, `BCM-primary`, `bcm_head_01`

**Why**: The deployment script auto-detects the BCM node by name pattern to configure cloud-init, SSH, and run the installation.

### 3. OOB Management (Optional but Recommended)

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

### 4. PXE Boot Nodes

**Naming**: PXE boot nodes (compute nodes) should follow a pattern like `cpu-01`, `compute-01`, `node-01`, etc.

**Configuration**: Use `"os": "pxe_boot_10G"` or `"os": "pxe"` for nodes that will PXE boot from BCM.

**Cloud-init**: PXE boot nodes are automatically skipped during cloud-init configuration since they boot from BCM.

## Optional Features

### Static MAC Address for Licensing

**Purpose**: BCM licenses are bound to MAC addresses. Using a static MAC on the BCM node ensures license consistency across simulation rebuilds.

**Example**:
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

**Note**: Keep this MAC address consistent across all your topologies to avoid license issues.

### OOB Management Switch

**Purpose**: An `oob-mgmt-switch` can be used to provide out-of-band management connectivity to all nodes.

**Example**: In `default.json`, all nodes connect to `oob-mgmt-switch` via `eth0`, while `eth4` on `bcm-01` provides outbound access.

## Validation

The deployment script validates:
- ✅ BCM node exists (name starts with `bcm`)
- ✅ BCM node has an interface connected to `"outbound"`
- ✅ Topology is in JSON format

If validation fails, the script will provide an error message explaining the issue.

## Example Topologies

| File | Description |
|------|-------------|
| `default.json` | Full lab with leaf switches, compute nodes, and OOB management |
| `test-bcm.json` | Minimal test topology for development |

