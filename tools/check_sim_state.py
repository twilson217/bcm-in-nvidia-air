#!/usr/bin/env python3
"""
Quick debug script to check simulation state via Air API
"""
import os
import sys
import requests
from dotenv import load_dotenv

# Load environment
load_dotenv()

api_base_url = os.getenv('AIR_API_URL', 'https://air-inside.nvidia.com')
username = os.getenv('AIR_USERNAME', os.getenv('USER'))
api_token = os.getenv('AIR_API_TOKEN')

if not api_token:
    print("ERROR: AIR_API_TOKEN not set")
    sys.exit(1)

# Get simulation ID from command line
if len(sys.argv) < 2:
    print("Usage: python check_sim_state.py <simulation_id>")
    sys.exit(1)

sim_id = sys.argv[1]

# Authenticate
print(f"Authenticating to {api_base_url}...")
login_response = requests.post(
    f"{api_base_url}/api/v1/login/",
    data={'username': username, 'password': api_token},
    timeout=30
)

if login_response.status_code != 200:
    print(f"Login failed: {login_response.status_code}")
    print(login_response.text)
    sys.exit(1)

token = login_response.json()['token']
headers = {'Authorization': f'Bearer {token}'}

print(f"\nChecking simulation: {sim_id}\n")

# Get simulation details
print("="*60)
print("SIMULATION DETAILS")
print("="*60)
sim_response = requests.get(
    f"{api_base_url}/api/v2/simulations/{sim_id}/",
    headers=headers,
    timeout=30
)

if sim_response.status_code == 200:
    sim_data = sim_response.json()
    print(f"Title: {sim_data.get('title')}")
    print(f"State: {sim_data.get('state')}")
    print(f"SSH Enabled: {sim_data.get('ssh_enabled')}")
    print(f"Created: {sim_data.get('created')}")
    print(f"Worker: {sim_data.get('worker_hostname', 'N/A')}")
    
    if 'services' in sim_data:
        print(f"\nServices: {len(sim_data.get('services', []))}")
        for svc in sim_data.get('services', []):
            print(f"  - {svc.get('service_type')}: {svc}")
else:
    print(f"Failed to get simulation: {sim_response.status_code}")
    print(sim_response.text)

# Get nodes
print("\n" + "="*60)
print("NODES")
print("="*60)
nodes_response = requests.get(
    f"{api_base_url}/api/v2/simulations/{sim_id}/nodes/",
    headers=headers,
    timeout=30
)

if nodes_response.status_code == 200:
    nodes_data = nodes_response.json()
    nodes = nodes_data.get('results', [])
    print(f"Total nodes: {len(nodes)}\n")
    
    for node in nodes:
        print(f"Node: {node.get('name')}")
        print(f"  ID: {node.get('id')}")
        print(f"  State: {node.get('state')}")
        print(f"  Function: {node.get('function')}")
        print(f"  Mgmt IP: {node.get('mgmt_ip')}")
        
        # Check for services on node interfaces
        if 'interfaces' in node:
            for iface in node.get('interfaces', []):
                if iface.get('services'):
                    print(f"  Interface {iface.get('name')} services:")
                    for svc in iface.get('services', []):
                        print(f"    - {svc.get('service_type')}: port {svc.get('external_port')}")
        print()
else:
    print(f"Failed to get nodes: {nodes_response.status_code}")
    print(nodes_response.text)

print("\n" + "="*60)
print("RAW SIMULATION JSON (relevant fields)")
print("="*60)
if sim_response.status_code == 200:
    import json
    # Print just the important fields
    relevant_fields = ['id', 'title', 'state', 'ssh_enabled', 'worker_hostname', 
                      'services', 'organization', 'created', 'updated']
    filtered_data = {k: v for k, v in sim_data.items() if k in relevant_fields}
    print(json.dumps(filtered_data, indent=2))

