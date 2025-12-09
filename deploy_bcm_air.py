#!/usr/bin/env python3
"""
NVIDIA Air BCM Deployment Automation

This script automates the deployment of Bright Cluster Manager (BCM) on NVIDIA Air
using stock Rocky Linux 9 images and Ansible Galaxy playbooks.
"""

import os
import sys
import time
import json
import argparse
import subprocess
import re
from datetime import datetime
from pathlib import Path
import requests
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class AirBCMDeployer:
    """Automate BCM deployment on NVIDIA Air"""
    
    def __init__(self, api_base_url="https://air.nvidia.com", api_token=None, username=None):
        """
        Initialize the deployer
        
        Args:
            api_base_url: NVIDIA Air base URL (without /api/vX)
            api_token: API authentication token
            username: Air account username/email
        """
        # Clean up URL - remove trailing slashes and /api/vX
        self.api_base_url = api_base_url.rstrip('/')
        if self.api_base_url.endswith('/api/v2'):
            self.api_base_url = self.api_base_url[:-7]
        if self.api_base_url.endswith('/api/v1'):
            self.api_base_url = self.api_base_url[:-7]
        
        self.api_token = api_token or os.getenv('AIR_API_TOKEN')
        self.username = username or os.getenv('AIR_USERNAME')
        
        if not self.api_token:
            print("\n✗ Error: AIR_API_TOKEN not found")
            print("\nPlease set your NVIDIA Air API token in .env file:")
            print("  AIR_API_TOKEN=your_token_here")
            print("\nTo get your API token:")
            print("  1. Log in to NVIDIA Air")
            print("  2. Go to your account settings")
            print("  3. Generate or copy your API token")
            raise ValueError("AIR_API_TOKEN must be set")
        
        if not self.username:
            print("\n✗ Error: AIR_USERNAME not found")
            print("\nPlease set your Air username in .env file:")
            print("  AIR_USERNAME=your_email@domain.com")
            raise ValueError("AIR_USERNAME must be set")
        
        # Authenticate and get JWT token
        self.jwt_token = self._authenticate()
        
        self.headers = {
            'Authorization': f'Bearer {self.jwt_token}',
            'Content-Type': 'application/json'
        }
        self.simulation_id = None
        self.bcm_node_id = None
    
    def _authenticate(self):
        """
        Authenticate with Air API to get JWT token
        
        Returns:
            JWT token string
        """
        login_url = f"{self.api_base_url}/api/v1/login/"
        
        try:
            response = requests.post(
                login_url,
                data={
                    'username': self.username,
                    'password': self.api_token
                },
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                if 'token' in result:
                    return result['token']
                else:
                    raise Exception(f"No token in login response: {result}")
            else:
                raise Exception(f"Login failed with status {response.status_code}: {response.text}")
                
        except Exception as e:
            print(f"\n✗ Authentication failed: {e}")
            print(f"\nTroubleshooting:")
            print(f"  1. Verify AIR_USERNAME is correct: {self.username}")
            print(f"  2. Verify AIR_API_TOKEN is valid for {self.api_base_url}")
            print(f"  3. For internal Air, ensure you're connected to VPN")
            raise
        
    def prompt_bcm_version(self):
        """Prompt user for BCM version selection"""
        print("\n" + "="*60)
        print("BCM Version Selection")
        print("="*60)
        print("\nPlease select the BCM version to install:")
        print("  1) BCM 10.x (brightcomputing.bcm100)")
        print("  2) BCM 11.x (brightcomputing.bcm110)")
        print()
        
        while True:
            choice = input("Enter your choice (1 or 2): ").strip()
            if choice == '1':
                return '10.x', 'brightcomputing.bcm100'
            elif choice == '2':
                return '11.x', 'brightcomputing.bcm110'
            else:
                print("Invalid choice. Please enter 1 or 2.")
    
    def read_dot_file(self, dot_file_path):
        """Read and return the contents of the .dot topology file"""
        with open(dot_file_path, 'r') as f:
            return f.read()
    
    def get_next_simulation_name(self):
        """
        Generate the next simulation name following the pattern YYYYMMNNN-BCM-Lab
        Checks existing simulations and increments the sequence number
        
        Returns:
            Default simulation name string
        """
        # Get current year and month
        now = datetime.now()
        year_month = now.strftime('%Y%m')  # YYYYMM format
        
        # Pattern to match our naming convention
        pattern = re.compile(rf'^{year_month}(\d{{3}})-BCM-Lab$')
        
        # Get list of existing simulations
        try:
            response = requests.get(
                f"{self.api_base_url}/api/v2/simulations/",
                headers=self.headers,
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                simulations = data.get('results', [])
                
                # Find all matching sequence numbers for this year-month
                sequence_numbers = []
                for sim in simulations:
                    title = sim.get('title', '')
                    match = pattern.match(title)
                    if match:
                        sequence_numbers.append(int(match.group(1)))
                
                # Get next sequence number
                if sequence_numbers:
                    next_seq = max(sequence_numbers) + 1
                else:
                    next_seq = 1
                
                return f"{year_month}{next_seq:03d}-BCM-Lab"
            else:
                # If we can't list simulations, use timestamp-based fallback
                return f"{year_month}001-BCM-Lab"
                
        except Exception as e:
            print(f"  Warning: Could not check existing simulations: {e}")
            # Fallback to timestamp-based name
            return f"{year_month}001-BCM-Lab"
    
    def prompt_simulation_name(self):
        """
        Prompt user for simulation name with smart default
        
        Returns:
            Simulation name string
        """
        default_name = self.get_next_simulation_name()
        
        print("\n" + "="*60)
        print("Simulation Name")
        print("="*60)
        print(f"\nDefault name: {default_name}")
        print("Press Enter to use default, or type a custom name:")
        
        user_input = input("> ").strip()
        
        if user_input:
            return user_input
        else:
            print(f"Using default name: {default_name}")
            return default_name
    
    def create_simulation(self, dot_file_path, simulation_name):
        """
        Create a simulation from a .dot file
        
        Args:
            dot_file_path: Path to the .dot topology file
            simulation_name: Name for the simulation
        
        Returns:
            Simulation ID
        """
        print("\n" + "="*60)
        print("Creating NVIDIA Air Simulation")
        print("="*60)
        
        dot_content = self.read_dot_file(dot_file_path)
        
        print(f"\nCreating simulation from DOT file: {simulation_name}")
        
        try:
            # Use the v2 simulation import endpoint
            payload = {
                'format': 'DOT',
                'title': simulation_name,
                'content': dot_content,
                # Optional: add ZTP script if available
                # 'ztp': ztp_script_content
            }
            
            response = requests.post(
                f"{self.api_base_url}/api/v2/simulations/import/",
                headers=self.headers,
                json=payload,
                timeout=60
            )
            
            print(f"Response status: {response.status_code}")
            if response.status_code in [200, 201]:
                result = response.json()
                # The import endpoint returns simulation details
                self.simulation_id = result.get('id')
                
                if self.simulation_id:
                    print(f"✓ Simulation created successfully!")
                    print(f"   ID: {self.simulation_id}")
                    print(f"   Title: {result.get('title', 'N/A')}")
                    print(f"   State: {result.get('state', 'N/A')}")
                    if 'nodes' in result:
                        node_count = len(result.get('nodes', []))
                        print(f"   Nodes: {node_count}")
                    return self.simulation_id
                else:
                    print(f"✗ No simulation ID in response: {result}")
                    raise Exception("Failed to get simulation ID from response")
            elif response.status_code == 401 or response.status_code == 403:
                print(f"\n✗ Authentication Failed: {response.status_code}")
                print(f"Response: {response.text}")
                print(f"\nYour API token is invalid or expired.")
                print(f"\nTroubleshooting:")
                print(f"  1. Check if AIR_API_TOKEN is set: echo $AIR_API_TOKEN")
                print(f"  2. Verify your token is valid in NVIDIA Air web interface")
                print(f"  3. Generate a new token if needed:")
                print(f"     - Log in to {self.api_base_url}")
                print(f"     - Go to Account Settings → API Tokens")
                print(f"     - Generate a new token and update AIR_API_TOKEN")
                print(f"  4. Make sure you're using the token for the correct Air site")
                print(f"     (internal vs external tokens are different)")
                raise Exception("Authentication failed")
            else:
                print(f"✗ Failed to create simulation: {response.status_code}")
                print(f"Response: {response.text}")
                print(f"Request URL: {self.api_base_url}/api/v2/simulations/import/")
                print(f"Request payload keys: {list(payload.keys())}")
                print(f"DOT file size: {len(dot_content)} bytes")
                raise Exception("Failed to create simulation")
                
        except requests.exceptions.ConnectionError as e:
            error_msg = str(e)
            if 'Failed to resolve' in error_msg or 'Name or service not known' in error_msg:
                print(f"\n✗ DNS Resolution Error: Cannot resolve hostname")
                print(f"\nThe Air site '{self.api_base_url}' cannot be reached.")
                if 'air-inside.nvidia.com' in self.api_base_url:
                    print(f"\nℹ  The internal Air site requires:")
                    print(f"   • Connection to NVIDIA internal network, OR")
                    print(f"   • Active NVIDIA VPN connection")
                    print(f"\nPlease check:")
                    print(f"   1. Are you connected to NVIDIA VPN?")
                    print(f"   2. Can you access https://air-inside.nvidia.com in a browser?")
                    print(f"   3. If you're not on NVIDIA network, use the external site:")
                    print(f"      python deploy_bcm_air.py (without --internal flag)")
                else:
                    print(f"\nPlease check:")
                    print(f"   1. Your internet connection")
                    print(f"   2. The API URL is correct: {self.api_base_url}")
                    print(f"   3. DNS resolution: try 'nslookup {self.api_base_url.split('/')[2]}'")
                raise
        except requests.exceptions.Timeout:
            print(f"\n✗ Connection timeout to {self.api_base_url}")
            print(f"The Air site is not responding. Please check your connection.")
            raise
    
    def wait_for_node_ready(self, node_name, timeout=600):
        """
        Wait for a specific node to be ready and SSH accessible
        
        Args:
            node_name: Name of the node to wait for
            timeout: Maximum time to wait in seconds
        
        Returns:
            Node details including IP address
        """
        print(f"\nWaiting for node '{node_name}' to be ready...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                response = requests.get(
                    f"{self.api_base_url}/api/v2/simulations/{self.simulation_id}/nodes/",
                    headers=self.headers,
                    timeout=30
                )
                
                if response.status_code == 200:
                    data = response.json()
                    # API returns paginated response with 'results' key
                    nodes = data.get('results', [])
                    
                    for node in nodes:
                        if node.get('name') == node_name:
                            state = node.get('state', 'unknown')
                            print(f"  Node '{node_name}' state: {state}                    ", end='\r')
                            
                            if state == 'READY' or state == 'RUNNING':
                                self.bcm_node_id = node.get('id')
                                print(f"\n✓ Node '{node_name}' is ready! (State: {state})")
                                return node
                    
                    # If we get here, node not found in results
                    if not nodes:
                        print(f"  No nodes found yet (simulation might still be initializing)...", end='\r')
                
                time.sleep(10)
            except Exception as e:
                print(f"  Error checking node status: {e}                    ", end='\r')
                time.sleep(10)
        
        raise Exception(f"Timeout waiting for node '{node_name}' to be ready")
    
    def get_node_ssh_info(self, node_id):
        """
        Get SSH connection information for a node
        
        Args:
            node_id: Node ID
        
        Returns:
            Dictionary with SSH connection details
        """
        response = requests.get(
            f"{self.api_base_url}/api/v2/nodes/{node_id}/console/",
            headers=self.headers
        )
        
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Warning: Could not retrieve SSH info: {response.status_code}")
            return None
    
    def start_simulation(self):
        """Start the simulation so nodes begin booting"""
        print("\nStarting simulation...")
        
        try:
            response = requests.post(
                f"{self.api_base_url}/api/v2/simulations/{self.simulation_id}/load/",
                headers=self.headers,
                timeout=30
            )
            
            if response.status_code in [200, 201, 204]:
                print("  ✓ Simulation started successfully")
                print("  ⏳ Nodes are now booting (this takes 2-5 minutes)...")
            else:
                print(f"  ✗ Failed to start simulation: {response.status_code}")
                print(f"  Response: {response.text}")
        except Exception as e:
            print(f"  Warning: Error starting simulation: {e}")
    
    def enable_ssh_service(self):
        """Enable SSH service for the simulation to allow direct SSH access"""
        print("\nEnabling SSH service for simulation...")
        
        try:
            # Get simulation details to enable services
            response = requests.patch(
                f"{self.api_base_url}/api/v2/simulations/{self.simulation_id}/",
                headers=self.headers,
                json={
                    'ssh_enabled': True
                },
                timeout=30
            )
            
            if response.status_code in [200, 201]:
                print("  ✓ SSH service enabled")
            else:
                print(f"  ✗ Failed to enable SSH: {response.status_code}")
                print(f"  Response: {response.text}")
        except Exception as e:
            print(f"  Warning: Error enabling SSH: {e}")
    
    def upload_ztp_script(self):
        """Upload ZTP script to the simulation for Cumulus switches"""
        print("\nUploading ZTP script for Cumulus switches...")
        
        ztp_file = Path(__file__).parent / 'ansible' / 'cumulus-ztp.sh'
        if not ztp_file.exists():
            print("  Warning: ansible/cumulus-ztp.sh not found, skipping ZTP upload")
            return
        
        with open(ztp_file, 'r') as f:
            ztp_content = f.read()
        
        # Update ZTP script for each Cumulus switch node
        switches = ['leaf01', 'leaf02']
        for switch in switches:
            try:
                payload = {
                    'ztp_script': ztp_content
                }
                response = requests.post(
                    f"{self.api_base_url}/api/v2/simulations/{self.simulation_id}/nodes/{switch}/ztp/",
                    headers=self.headers,
                    json=payload
                )
                
                if response.status_code in [200, 201]:
                    print(f"  ✓ ZTP script uploaded for {switch}")
                else:
                    print(f"  ✗ Failed to upload ZTP for {switch}: {response.status_code}")
            except Exception as e:
                print(f"  Warning: Error uploading ZTP for {switch}: {e}")
    
    def execute_ansible_playbook(self, bcm_version, collection_name):
        """
        Execute Ansible playbook to install BCM
        
        Args:
            bcm_version: BCM version string (10.x or 11.x)
            collection_name: Ansible Galaxy collection name
        """
        print("\n" + "="*60)
        print(f"Installing BCM {bcm_version} via Ansible")
        print("="*60)
        
        # Create temporary inventory file
        inventory_content = f"""[bcm_headnode]
bcm-headnode0 ansible_host=192.168.200.254 ansible_user=root ansible_ssh_common_args='-o StrictHostKeyChecking=no'

[all:vars]
bcm_version={bcm_version}
bcm_collection={collection_name}
"""
        
        inventory_file = Path('/tmp/bcm_inventory.ini')
        with open(inventory_file, 'w') as f:
            f.write(inventory_content)
        
        print(f"\n✓ Inventory file created")
        print(f"Running Ansible playbook (this may take 10-15 minutes)...\n")
        
        # Run ansible-playbook command
        playbook_path = Path(__file__).parent / 'ansible' / 'install_bcm.yml'
        
        cmd = [
            'ansible-playbook',
            '-i', str(inventory_file),
            str(playbook_path),
            '-e', f'bcm_version={bcm_version}',
            '-e', f'bcm_collection={collection_name}'
        ]
        
        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=False,
                text=True
            )
            print("\n✓ BCM installation completed successfully!")
        except subprocess.CalledProcessError as e:
            print(f"\n✗ Ansible playbook failed with exit code {e.returncode}")
            raise
    
    def print_summary(self, bcm_version):
        """Print deployment summary and next steps"""
        print("\n" + "="*60)
        print("Deployment Complete!")
        print("="*60)
        
        print(f"\nBCM {bcm_version} has been deployed on NVIDIA Air")
        print(f"\nSimulation ID: {self.simulation_id}")
        print(f"\nTo access BCM:")
        print(f"  1. Connect to the simulation via NVIDIA Air web interface")
        print(f"  2. SSH to bcm-headnode0 (192.168.200.254)")
        print(f"     Username: root")
        print(f"     Password: 3tango")
        print(f"\nNext Steps:")
        print(f"  1. Disable DHCP on oob-mgmt-server:")
        print(f"     sudo systemctl disable isc-dhcp-server")
        print(f"     sudo service isc-dhcp-server stop")
        print(f"  2. Configure BCM network gateway:")
        print(f"     cmsh")
        print(f"     network; use internalnet; set gateway 192.168.200.1; commit")
        print(f"  3. Add switches and compute nodes to BCM (see README.md)")
        print(f"\nFor BCM GUI access:")
        print(f"  Add a service in Air to expose TCP 8081 on bcm-headnode0")
        print(f"  Access at: https://<worker_url>:<port>/userportal")
        print("\n" + "="*60 + "\n")


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Automate BCM deployment on NVIDIA Air',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Deploy with API token from environment (external site)
  export AIR_API_TOKEN=your_token_here
  python deploy_bcm_air.py
  
  # Deploy to internal NVIDIA Air site
  export AIR_API_TOKEN=your_token_here
  python deploy_bcm_air.py --internal
  
  # Deploy with custom API URL
  export AIR_API_TOKEN=your_token_here
  export AIR_API_URL=https://air-inside.nvidia.com
  python deploy_bcm_air.py
  
  # Deploy with custom simulation name (or will prompt)
  python deploy_bcm_air.py --name my-bcm-lab
  
  # Deploy and let it auto-generate name (202512001-BCM-Lab, 202512002-BCM-Lab, etc.)
  python deploy_bcm_air.py
        """
    )
    
    parser.add_argument(
        '--api-token',
        help='NVIDIA Air API token (or set AIR_API_TOKEN env var)'
    )
    parser.add_argument(
        '--api-url',
        help='NVIDIA Air API base URL (or set AIR_API_URL env var). Default: https://air.nvidia.com'
    )
    parser.add_argument(
        '--internal',
        action='store_true',
        help='Use internal NVIDIA Air site (air-inside.nvidia.com)'
    )
    parser.add_argument(
        '--dot-file',
        default='topologies/test-bcm.dot',
        help='Path to topology .dot file (default: topologies/test-bcm.dot)'
    )
    parser.add_argument(
        '--name',
        help='Custom simulation name (will prompt if not provided, default: YYYYMMNNN-BCM-Lab)'
    )
    parser.add_argument(
        '--skip-ansible',
        action='store_true',
        help='Skip Ansible installation (create simulation only)'
    )
    
    args = parser.parse_args()
    
    # Determine API URL with priority: --api-url > --internal > AIR_API_URL env var > default
    if args.api_url:
        api_base_url = args.api_url
    elif args.internal:
        api_base_url = 'https://air-inside.nvidia.com'
    else:
        api_base_url = os.getenv('AIR_API_URL', 'https://air.nvidia.com')
    
    try:
        # Initialize deployer
        print("\n" + "="*60)
        print("NVIDIA Air BCM Automated Deployment")
        print("="*60)
        print(f"Using API: {api_base_url}")
        
        deployer = AirBCMDeployer(
            api_base_url=api_base_url,
            api_token=args.api_token,
            username=None  # Will load from env
        )
        
        # Prompt for BCM version
        bcm_version, collection_name = deployer.prompt_bcm_version()
        
        # Prompt for simulation name (or use command-line arg if provided)
        if args.name:
            simulation_name = args.name
            print(f"\nUsing simulation name from command line: {simulation_name}")
        else:
            simulation_name = deployer.prompt_simulation_name()
        
        # Create simulation
        dot_file = Path(args.dot_file)
        if not dot_file.exists():
            print(f"\n✗ Error: Topology file not found: {dot_file}")
            sys.exit(1)
        
        deployer.create_simulation(dot_file, simulation_name)
        
        # Enable SSH service for direct access
        deployer.enable_ssh_service()
        
        # Start the simulation
        deployer.start_simulation()
        
        # Upload ZTP script
        deployer.upload_ztp_script()
        
        # Wait for BCM node to be ready
        bcm_node = deployer.wait_for_node_ready('bcm-headnode0', timeout=900)
        
        if not args.skip_ansible:
            # Execute Ansible playbook
            deployer.execute_ansible_playbook(bcm_version, collection_name)
        else:
            print("\n--skip-ansible specified, skipping BCM installation")
        
        # Print summary
        deployer.print_summary(bcm_version)
        
        return 0
        
    except KeyboardInterrupt:
        print("\n\nDeployment interrupted by user")
        return 130
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())

