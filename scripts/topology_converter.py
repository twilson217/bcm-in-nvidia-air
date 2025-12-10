#!/usr/bin/env python3
"""
Topology Converter: DOT to JSON

Converts NVIDIA Air DOT topology files to JSON format with OOB disabled.
This is necessary because DOT format doesn't support disabling the automatic
OOB management network, which reserves eth0 on all nodes.

Usage:
    python scripts/topology_converter.py topologies/my-lab.dot
    python scripts/topology_converter.py topologies/my-lab.dot -o topologies/my-lab.json
    python scripts/topology_converter.py topologies/my-lab.dot --bcm-mac 48:b0:2d:00:00:00
"""

import argparse
import json
import re
import sys
from pathlib import Path


def parse_dot_attributes(attr_string):
    """
    Parse DOT node attributes like: memory="8192" os="generic/ubuntu2404" cpu="4"
    
    Returns:
        dict of attribute name -> value
    """
    attributes = {}
    # Match key="value" or key='value' patterns
    pattern = r'(\w+)\s*=\s*"([^"]*)"|(\w+)\s*=\s*\'([^\']*)\''
    
    for match in re.finditer(pattern, attr_string):
        if match.group(1):  # Double quotes
            key, value = match.group(1), match.group(2)
        else:  # Single quotes
            key, value = match.group(3), match.group(4)
        
        # Convert numeric values
        if value.isdigit():
            value = int(value)
        
        attributes[key] = value
    
    return attributes


def parse_dot_file(dot_path):
    """
    Parse a DOT topology file.
    
    Returns:
        title: Graph title
        nodes: dict of node_name -> attributes
        links: list of (node1, iface1, node2, iface2) tuples
    """
    with open(dot_path, 'r') as f:
        content = f.read()
    
    # Extract graph title
    title_match = re.search(r'graph\s+"([^"]+)"', content)
    title = title_match.group(1) if title_match else "topology"
    
    nodes = {}
    links = []
    
    # Parse node definitions: "node-name" [attr1="val1" attr2="val2"]
    node_pattern = r'"([^"]+)"\s*\[([^\]]+)\]'
    for match in re.finditer(node_pattern, content):
        node_name = match.group(1)
        attr_string = match.group(2)
        
        # Skip fake nodes (workaround nodes for DOT format)
        attributes = parse_dot_attributes(attr_string)
        if attributes.get('function') == 'fake':
            continue
        
        nodes[node_name] = attributes
    
    # Parse link definitions: "node1":"iface1" -- "node2":"iface2"
    # Also handle: "node1":"iface1" -- "outbound"
    link_pattern = r'"([^"]+)":"([^"]+)"\s*--\s*(?:"([^"]+)":"([^"]+)"|"([^"]+)")'
    for match in re.finditer(link_pattern, content):
        node1 = match.group(1)
        iface1 = match.group(2)
        
        if match.group(3):  # node:interface format
            node2 = match.group(3)
            iface2 = match.group(4)
        else:  # Simple string like "outbound"
            node2 = match.group(5)
            iface2 = None
        
        # Skip links involving fake nodes
        if node1.startswith('fake') or (node2 and node2.startswith('fake')):
            # But preserve the outbound connection concept
            if node2 == 'outbound' or iface2 == 'outbound':
                # This was a workaround - the real intent is node1:iface -> outbound
                # We'll add this as a direct outbound connection
                pass
            continue
        
        links.append((node1, iface1, node2, iface2))
    
    return title, nodes, links


def detect_bcm_node(nodes):
    """
    Detect the BCM head node from the node list.
    
    Returns:
        Node name starting with 'bcm', or None
    """
    bcm_nodes = [name for name in nodes.keys() if name.lower().startswith('bcm')]
    if bcm_nodes:
        # Return the one with lowest number
        return sorted(bcm_nodes)[0]
    return None


def convert_to_json(title, nodes, links, bcm_mac=None):
    """
    Convert parsed DOT topology to JSON format with OOB disabled.
    
    Args:
        title: Graph title
        nodes: dict of node_name -> attributes
        links: list of link tuples
        bcm_mac: Optional MAC address for BCM node's eth0
    
    Returns:
        JSON-compatible dict
    """
    bcm_node = detect_bcm_node(nodes)
    
    # Build nodes section
    json_nodes = {}
    for node_name, attrs in nodes.items():
        node_def = {"oob": False}  # Always disable OOB
        
        # Map DOT attributes to JSON
        attr_mapping = {
            'cpu': 'cpu',
            'memory': 'memory',
            'storage': 'storage',
            'os': 'os',
            'cpu_mode': 'cpu_mode',
            'function': 'function',
            'boot': 'boot',
            'mgmt_ip': 'mgmt_ip',  # Preserve management IP
        }
        
        for dot_attr, json_attr in attr_mapping.items():
            if dot_attr in attrs:
                node_def[json_attr] = attrs[dot_attr]
        
        json_nodes[node_name] = node_def
    
    # Build links section
    json_links = []
    bcm_has_outbound = False
    
    for node1, iface1, node2, iface2 in links:
        if node2 == 'outbound' or iface2 == 'outbound':
            # Direct outbound connection
            link_def = [{"interface": iface1, "node": node1}, "outbound"]
            if node1 == bcm_node and bcm_mac:
                link_def[0]["mac"] = bcm_mac
            bcm_has_outbound = True
        elif iface2 is None:
            # Simple connection to named endpoint
            link_def = [{"interface": iface1, "node": node1}, node2]
        else:
            # Node-to-node connection
            link_def = [
                {"interface": iface1, "node": node1},
                {"interface": iface2, "node": node2}
            ]
        json_links.append(link_def)
    
    # If BCM node exists but has no outbound connection, add one on eth0
    if bcm_node and not bcm_has_outbound:
        outbound_link = [{"interface": "eth0", "node": bcm_node}, "outbound"]
        if bcm_mac:
            outbound_link[0]["mac"] = bcm_mac
        json_links.insert(0, outbound_link)
        print(f"  ℹ Added outbound connection for {bcm_node}:eth0")
    
    # Build final JSON structure
    return {
        "format": "JSON",
        "title": title,
        "content": {
            "nodes": json_nodes,
            "links": json_links,
            "oob": False,
            "netq": False
        }
    }


def main():
    parser = argparse.ArgumentParser(
        description='Convert NVIDIA Air DOT topology to JSON format with OOB disabled',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Convert and auto-name output (my-lab.dot -> my-lab.json)
  python scripts/topology_converter.py topologies/my-lab.dot

  # Specify output file
  python scripts/topology_converter.py topologies/my-lab.dot -o topologies/output.json

  # Set BCM node MAC address for licensing
  python scripts/topology_converter.py topologies/my-lab.dot --bcm-mac 48:b0:2d:00:00:00
        """
    )
    
    parser.add_argument(
        'dot_file',
        help='Input DOT topology file'
    )
    parser.add_argument(
        '-o', '--output',
        help='Output JSON file (default: same name with .json extension)'
    )
    parser.add_argument(
        '--bcm-mac',
        default='48:b0:2d:00:00:00',
        help='MAC address for BCM node eth0 (for license consistency). Default: 48:b0:2d:00:00:00'
    )
    parser.add_argument(
        '--no-bcm-mac',
        action='store_true',
        help='Do not set a static MAC on BCM node'
    )
    
    args = parser.parse_args()
    
    dot_path = Path(args.dot_file)
    if not dot_path.exists():
        print(f"✗ Error: File not found: {dot_path}")
        sys.exit(1)
    
    # Determine output path
    if args.output:
        json_path = Path(args.output)
    else:
        json_path = dot_path.with_suffix('.json')
    
    print(f"\n{'='*60}")
    print("DOT to JSON Topology Converter")
    print(f"{'='*60}")
    print(f"  Input:  {dot_path}")
    print(f"  Output: {json_path}")
    
    # Parse DOT file
    print(f"\nParsing DOT file...")
    try:
        title, nodes, links = parse_dot_file(dot_path)
    except Exception as e:
        print(f"✗ Error parsing DOT file: {e}")
        sys.exit(1)
    
    print(f"  ✓ Title: {title}")
    print(f"  ✓ Nodes: {len(nodes)}")
    print(f"  ✓ Links: {len(links)}")
    
    # Detect BCM node
    bcm_node = detect_bcm_node(nodes)
    if bcm_node:
        print(f"  ✓ BCM node detected: {bcm_node}")
    else:
        print(f"  ⚠ No BCM node detected (expected node name starting with 'bcm')")
    
    # Convert to JSON
    print(f"\nConverting to JSON format...")
    bcm_mac = None if args.no_bcm_mac else args.bcm_mac
    json_data = convert_to_json(title, nodes, links, bcm_mac)
    
    print(f"  ✓ OOB disabled globally")
    print(f"  ✓ OOB disabled on all {len(nodes)} nodes")
    if bcm_mac and bcm_node:
        print(f"  ✓ BCM MAC address: {bcm_mac}")
    
    # Write JSON file
    print(f"\nWriting JSON file...")
    try:
        with open(json_path, 'w') as f:
            json.dump(json_data, f, indent=4)
        print(f"  ✓ Saved to: {json_path}")
    except Exception as e:
        print(f"✗ Error writing JSON file: {e}")
        sys.exit(1)
    
    print(f"\n{'='*60}")
    print("Conversion complete!")
    print(f"{'='*60}")
    print(f"\nUse with deploy script:")
    print(f"  python deploy_bcm_air.py --topology {json_path}")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())

