#!/usr/bin/env python3
"""
Topology Validation Script

Validates NVIDIA Air topology JSON files against BCM deployment requirements.
Run this before deployment to catch configuration issues early.

Usage:
    python scripts/topology_validation.py topologies/default.json
    python scripts/topology_validation.py topologies/*.json  # Validate multiple
"""

import argparse
import json
import sys
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


class TopologyValidator:
    """Validate topology files against BCM deployment requirements"""
    
    def __init__(self, topology_path):
        self.path = Path(topology_path)
        self.data = None
        self.nodes = {}
        self.links = []
        self.errors = []
        self.warnings = []
        self.info = []
        
    def load(self):
        """Load and parse the topology file"""
        if not self.path.exists():
            self.errors.append(f"File not found: {self.path}")
            return False
        
        if self.path.suffix.lower() != '.json':
            self.errors.append(f"Not a JSON file: {self.path}")
            return False
        
        try:
            with open(self.path) as f:
                self.data = json.load(f)
        except json.JSONDecodeError as e:
            self.errors.append(f"Invalid JSON: {e}")
            return False
        
        self.nodes = self.data.get('content', {}).get('nodes', {})
        self.links = self.data.get('content', {}).get('links', [])
        return True
    
    def find_bcm_node(self):
        """Find the BCM head node"""
        bcm_nodes = [name for name in self.nodes.keys() 
                     if name.lower().startswith('bcm')]
        
        if not bcm_nodes:
            self.errors.append("No BCM node found (name must start with 'bcm')")
            return None
        
        if len(bcm_nodes) > 1:
            self.info.append(f"Multiple BCM nodes found: {bcm_nodes}")
        
        # Return the one with lowest number
        bcm_nodes.sort()
        return bcm_nodes[0]
    
    def find_node_connections(self, node_name):
        """Find all connections for a specific node"""
        connections = {}
        
        for link in self.links:
            if len(link) != 2:
                continue
            
            endpoint1, endpoint2 = link
            
            # Check if this node is endpoint1
            if isinstance(endpoint1, dict) and endpoint1.get('node') == node_name:
                iface = endpoint1.get('interface')
                if isinstance(endpoint2, dict):
                    connections[iface] = endpoint2.get('node')
                else:
                    connections[iface] = endpoint2  # "outbound", "unconnected", etc.
            
            # Check if this node is endpoint2
            if isinstance(endpoint2, dict) and endpoint2.get('node') == node_name:
                iface = endpoint2.get('interface')
                if isinstance(endpoint1, dict):
                    connections[iface] = endpoint1.get('node')
                else:
                    connections[iface] = endpoint1
        
        return connections
    
    def is_pxe_boot_node(self, node_name):
        """
        Determine if a node is configured for PXE boot (as a client).
        
        Detection criteria (in order of reliability):
        1. "boot": "network" - explicitly set to network boot
        2. "os" contains "pxe" - OS is a PXE boot image
        
        Note: "pxehost" indicates if the node is a PXE SERVER, not client.
        pxehost=false means "this node doesn't serve PXE" (most nodes).
        """
        node = self.nodes.get(node_name, {})
        
        # Check for explicit network boot setting
        if node.get('boot') == 'network':
            return True
        
        # Check for PXE OS
        node_os = node.get('os', '')
        if isinstance(node_os, str) and 'pxe' in node_os.lower():
            return True
        
        return False
    
    def is_switch_node(self, node_name):
        """Determine if a node is a network switch"""
        node = self.nodes.get(node_name, {})
        
        # Check function attribute
        function = node.get('function', '').lower()
        if function in ['leaf', 'spine', 'switch', 'oob-switch']:
            return True
        
        # Check OS for switch indicators
        node_os = node.get('os', '').lower()
        if 'cumulus' in node_os or 'sonic' in node_os or 'switch' in node_os:
            return True
        
        # Check name patterns
        name_lower = node_name.lower()
        if any(x in name_lower for x in ['leaf', 'spine', 'switch', 'tor', 'agg']):
            return True
        
        return False
    
    def validate_bcm_outbound(self, bcm_node):
        """Validate BCM node has outbound connection on eth0"""
        connections = self.find_node_connections(bcm_node)
        
        outbound_iface = None
        for iface, target in connections.items():
            if target == 'outbound':
                outbound_iface = iface
                break
        
        if not outbound_iface:
            self.errors.append(
                f"BCM node '{bcm_node}' has no interface connected to 'outbound'\n"
                f"   This is REQUIRED for SSH access and external connectivity"
            )
            return None
        
        # CRITICAL: eth0 must be the outbound interface due to NVIDIA Air's netplan
        if outbound_iface != 'eth0':
            self.errors.append(
                f"BCM outbound interface must be 'eth0', but found '{outbound_iface}'\n"
                f"   NVIDIA Air's 40-air.yaml netplan only configures eth0 for DHCP.\n"
                f"   Using other interfaces causes hostname and IP assignment failures."
            )
            return outbound_iface
        
        self.info.append(f"BCM outbound interface: {bcm_node}:{outbound_iface} (correct)")
        return outbound_iface
    
    def validate_bcm_management(self, bcm_node):
        """Validate BCM node has internalnet interface (legacy: oob-mgmt-switch connection)"""
        connections = self.find_node_connections(bcm_node)

        internalnet_if = (os.getenv("BCM_INTERNALNET_IF") or "").strip() or None
        if internalnet_if:
            if internalnet_if in connections:
                self.info.append(f"BCM internalnet interface (BCM_INTERNALNET_IF): {bcm_node}:{internalnet_if}")
            else:
                self.warnings.append(
                    f"BCM_INTERNALNET_IF is set to '{internalnet_if}', but that interface is not present on {bcm_node} connections.\n"
                    f"   Found interfaces: {sorted([k for k in connections.keys() if k])}\n"
                    f"   deploy_bcm_air.py will accept BCM_INTERNALNET_IF blindly; double-check your topology links."
                )
            return internalnet_if

        # Legacy/back-compat: prefer oob-mgmt-switch if present
        legacy_iface = None
        for iface, target in connections.items():
            if target == 'oob-mgmt-switch':
                legacy_iface = iface
                break

        if legacy_iface:
            self.info.append(f"BCM internalnet interface (legacy): {bcm_node}:{legacy_iface} → oob-mgmt-switch")
            return legacy_iface

        self.warnings.append(
            f"BCM node '{bcm_node}' has no interface connected to 'oob-mgmt-switch' (legacy).\n"
            f"   deploy_bcm_air.py will default internalnet to eth1 when BCM_INTERNALNET_IF is not set."
        )
        return None
    
    def validate_oob_disabled(self):
        """Check if OOB is disabled (recommended)"""
        global_oob = self.data.get('content', {}).get('oob', True)
        
        if global_oob:
            self.warnings.append(
                "Global OOB is enabled ('oob': true)\n"
                "   Consider setting 'oob': false for full interface control"
            )
        else:
            self.info.append("Global OOB is disabled (recommended)")
        
        return not global_oob
    
    def validate_pxe_nodes(self):
        """Validate and list PXE boot nodes"""
        pxe_nodes = []
        
        for node_name in self.nodes:
            if self.is_pxe_boot_node(node_name):
                node = self.nodes[node_name]
                pxe_nodes.append({
                    'name': node_name,
                    'os': node.get('os', 'N/A'),
                    'boot': node.get('boot', 'N/A'),
                    'pxehost': node.get('pxehost', 'N/A')
                })
        
        if pxe_nodes:
            self.info.append(f"PXE boot nodes detected: {len(pxe_nodes)}")
            for pn in pxe_nodes:
                self.info.append(f"  - {pn['name']}: os={pn['os']}, boot={pn['boot']}")
        
        return pxe_nodes
    
    def validate_switches(self):
        """Validate and list switch nodes"""
        switches = []
        
        for node_name in self.nodes:
            if self.is_switch_node(node_name):
                node = self.nodes[node_name]
                switches.append({
                    'name': node_name,
                    'os': node.get('os', 'N/A'),
                    'function': node.get('function', 'N/A')
                })
        
        if switches:
            self.info.append(f"Switch nodes detected: {len(switches)}")
        
        return switches
    
    def validate(self):
        """Run all validations"""
        print(f"\n{'='*60}")
        print(f"Validating: {self.path.name}")
        print(f"{'='*60}")
        
        if not self.load():
            return False
        
        print(f"\nTopology: {self.data.get('title', 'Untitled')}")
        print(f"Nodes: {len(self.nodes)}")
        print(f"Links: {len(self.links)}")
        
        # Find BCM node
        bcm_node = self.find_bcm_node()
        if bcm_node:
            self.info.append(f"BCM node: {bcm_node}")
            
            # Validate BCM connections
            self.validate_bcm_outbound(bcm_node)
            self.validate_bcm_management(bcm_node)
        
        # Validate OOB settings
        self.validate_oob_disabled()
        
        # Detect node types
        self.validate_pxe_nodes()
        self.validate_switches()
        
        # Print results
        if self.info:
            print(f"\n✓ Info:")
            for msg in self.info:
                for line in msg.split('\n'):
                    print(f"    {line}")
        
        if self.warnings:
            print(f"\n⚠ Warnings:")
            for msg in self.warnings:
                for line in msg.split('\n'):
                    print(f"    {line}")
        
        if self.errors:
            print(f"\n✗ Errors:")
            for msg in self.errors:
                for line in msg.split('\n'):
                    print(f"    {line}")
            return False
        
        print(f"\n✓ Validation passed!")
        return True


def main():
    parser = argparse.ArgumentParser(
        description='Validate NVIDIA Air topology files for BCM deployment',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python scripts/topology_validation.py topologies/default.json
    python scripts/topology_validation.py topologies/*.json
        """
    )
    
    parser.add_argument(
        'topology_files',
        nargs='+',
        help='Topology JSON file(s) to validate'
    )
    
    parser.add_argument(
        '-q', '--quiet',
        action='store_true',
        help='Only show errors and warnings'
    )
    
    args = parser.parse_args()

    # Load .env if present so validation can consider BCM_INTERNALNET_* overrides.
    if load_dotenv is not None:
        try:
            load_dotenv()
        except Exception:
            pass
    
    all_passed = True
    
    for topology_file in args.topology_files:
        validator = TopologyValidator(topology_file)
        if not validator.validate():
            all_passed = False
    
    print(f"\n{'='*60}")
    if all_passed:
        print("All topologies validated successfully!")
        return 0
    else:
        print("Some validations failed - see errors above")
        return 1


if __name__ == '__main__':
    sys.exit(main())

