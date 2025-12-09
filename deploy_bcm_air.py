#!/usr/bin/env python3
"""
NVIDIA Air BCM Deployment Automation

This script automates the deployment of Bright Cluster Manager (BCM) on NVIDIA Air
using stock Ubuntu 24.04 images and Ansible Galaxy playbooks.
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
        
        # Load SSH key paths from environment
        self.ssh_private_key = os.getenv('SSH_PRIVATE_KEY', '~/.ssh/id_rsa')
        self.ssh_public_key = os.getenv('SSH_PUBLIC_KEY', '~/.ssh/id_rsa.pub')
        
        # Expand ~ to home directory
        self.ssh_private_key = os.path.expanduser(self.ssh_private_key)
        self.ssh_public_key = os.path.expanduser(self.ssh_public_key)
        
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
    
    def prompt_default_password(self):
        """Prompt user for default password for nodes"""
        print("\n" + "="*60)
        print("Default Password Configuration")
        print("="*60)
        print("\nSet the default password for all nodes in the simulation.")
        print(f"Default: Nvidia1234!")
        print("Press Enter to use default, or type a custom password:")
        
        user_input = input("> ").strip()
        
        if user_input:
            self.default_password = user_input
            print(f"Using custom password: {user_input}")
        else:
            self.default_password = "Nvidia1234!"
            print(f"Using default password: Nvidia1234!")
        
        return self.default_password
    
    def ensure_cloud_init_config(self):
        """
        Ensure cloud-init-password.yaml exists by generating it from template.
        Uses SSH_PUBLIC_KEY from .env to populate the SSH key.
        
        Returns:
            Path to cloud-init config file
        """
        cloudinit_path = Path(__file__).parent / 'cloud-init-password.yaml'
        template_path = Path(__file__).parent / 'cloud-init-password.yaml.example'
        
        if cloudinit_path.exists():
            print(f"  ✓ Using existing cloud-init config: {cloudinit_path.name}")
            return cloudinit_path
        
        # Need to generate from template
        if not template_path.exists():
            print(f"  ✗ Cloud-init template not found: {template_path}")
            raise FileNotFoundError(f"Missing template: {template_path}")
        
        # Read public key
        ssh_pub_key_path = Path(self.ssh_public_key)
        if not ssh_pub_key_path.exists():
            print(f"\n✗ SSH public key not found: {self.ssh_public_key}")
            print(f"\nPlease check SSH_PUBLIC_KEY in your .env file.")
            print(f"Current value: {self.ssh_public_key}")
            raise FileNotFoundError(f"SSH public key not found: {self.ssh_public_key}")
        
        ssh_public_key_content = ssh_pub_key_path.read_text().strip()
        
        # Read template and replace placeholder
        template_content = template_path.read_text()
        
        # Replace the placeholder with actual key
        cloudinit_content = template_content.replace('YOUR_SSH_PUBLIC_KEY_HERE', ssh_public_key_content)
        
        # Write the generated config
        cloudinit_path.write_text(cloudinit_content)
        
        print(f"  ✓ Generated cloud-init config from template")
        print(f"    SSH public key: {self.ssh_public_key}")
        
        return cloudinit_path
    
    def read_dot_file(self, dot_file_path):
        """Read and return the contents of the .dot topology file"""
        with open(dot_file_path, 'r') as f:
            return f.read()
    
    def detect_bcm_nodes(self, dot_content):
        """
        Detect BCM node(s) from the topology file.
        Looks for nodes named like 'bcm-01', 'bcm-02', 'bcm-headnode0', etc.
        Returns the primary BCM node (lowest number if multiple exist).
        
        Args:
            dot_content: String content of the DOT file
            
        Returns:
            String name of the primary BCM node
            
        Raises:
            Exception if no BCM node is found
        """
        import re
        
        # Pattern to match node definitions: "node-name" [attributes]
        node_pattern = r'"([^"]+)"\s*\['
        
        # Find all node names
        nodes = re.findall(node_pattern, dot_content)
        
        # Filter for BCM nodes (start with 'bcm' or 'bcm-')
        bcm_nodes = []
        for node in nodes:
            # Match patterns like: bcm-01, bcm-headnode0, bcm01, bcm-head-01, etc.
            if re.match(r'^bcm[-_]?', node, re.IGNORECASE):
                bcm_nodes.append(node)
        
        if not bcm_nodes:
            raise Exception(
                "No BCM node found in topology file.\n"
                "Expected a node starting with 'bcm' (e.g., 'bcm-01', 'bcm-headnode0').\n"
                "See README for topology file guidelines."
            )
        
        # If multiple BCM nodes, sort and pick the one with lowest number
        if len(bcm_nodes) > 1:
            # Extract numbers from node names and sort
            def get_node_number(name):
                numbers = re.findall(r'\d+', name)
                return int(numbers[0]) if numbers else 999
            
            bcm_nodes.sort(key=get_node_number)
            print(f"\n  ℹ Multiple BCM nodes detected: {', '.join(bcm_nodes)}")
            print(f"  ℹ Using primary node: {bcm_nodes[0]}")
        
        self.bcm_node_name = bcm_nodes[0]
        return bcm_nodes[0]
    
    def detect_bcm_nodes_json(self, nodes_dict):
        """
        Detect BCM node(s) from a JSON topology nodes dictionary.
        Looks for nodes named like 'bcm-01', 'bcm-02', 'bcm-headnode0', etc.
        Returns the primary BCM node (lowest number if multiple exist).
        
        Args:
            nodes_dict: Dictionary of nodes from JSON topology content
            
        Returns:
            String name of the primary BCM node
            
        Raises:
            Exception if no BCM node is found
        """
        import re
        
        # Filter for BCM nodes (start with 'bcm' or 'bcm-')
        bcm_nodes = []
        for node_name in nodes_dict.keys():
            if re.match(r'^bcm[-_]?', node_name, re.IGNORECASE):
                bcm_nodes.append(node_name)
        
        if not bcm_nodes:
            raise Exception(
                "No BCM node found in topology file.\n"
                "Expected a node starting with 'bcm' (e.g., 'bcm-01', 'bcm-headnode0').\n"
                "See README for topology file guidelines."
            )
        
        # If multiple BCM nodes, sort and pick the one with lowest number
        if len(bcm_nodes) > 1:
            def get_node_number(name):
                numbers = re.findall(r'\d+', name)
                return int(numbers[0]) if numbers else 999
            
            bcm_nodes.sort(key=get_node_number)
            print(f"\n  ℹ Multiple BCM nodes detected: {', '.join(bcm_nodes)}")
            print(f"  ℹ Using primary node: {bcm_nodes[0]}")
        
        self.bcm_node_name = bcm_nodes[0]
        return bcm_nodes[0]
    
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
    
    def create_simulation(self, topology_file_path, simulation_name):
        """
        Create a simulation from a topology file (.dot or .json)
        
        Args:
            topology_file_path: Path to the topology file (.dot or .json)
            simulation_name: Name for the simulation
        
        Returns:
            Simulation ID
        """
        print("\n" + "="*60)
        print("Creating NVIDIA Air Simulation")
        print("="*60)
        
        topology_path = Path(topology_file_path)
        file_ext = topology_path.suffix.lower()
        
        # Prepare payload based on file format
        if file_ext == '.json':
            # JSON format - read and parse
            with open(topology_path, 'r') as f:
                topology_data = json.load(f)
            
            # Detect BCM node from JSON topology
            nodes = topology_data.get('content', {}).get('nodes', {})
            bcm_node = self.detect_bcm_nodes_json(nodes)
            print(f"\n  ✓ Detected BCM node: {bcm_node}")
            
            print(f"\nCreating simulation from JSON file: {simulation_name}")
            
            # Override title with our simulation name
            topology_data['title'] = simulation_name
            payload = topology_data
            content_size = len(json.dumps(topology_data))
            
        else:
            # DOT format (default)
            dot_content = self.read_dot_file(topology_file_path)
            
            # Detect BCM node from DOT topology
            bcm_node = self.detect_bcm_nodes(dot_content)
            print(f"\n  ✓ Detected BCM node: {bcm_node}")
            
            print(f"\nCreating simulation from DOT file: {simulation_name}")
            
            payload = {
                'format': 'DOT',
                'title': simulation_name,
                'content': dot_content,
            }
            content_size = len(dot_content)
        
        try:
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
                print(f"Topology file size: {content_size} bytes")
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
        print(f"  Checking for states: READY, RUNNING, LOADED, STARTED, BOOTED, UP")
        start_time = time.time()
        last_state = None
        check_count = 0
        first_check = True
        
        while time.time() - start_time < timeout:
            try:
                response = requests.get(
                    f"{self.api_base_url}/api/v2/simulations/nodes/",
                    headers=self.headers,
                    timeout=30
                )
                
                if response.status_code == 200:
                    data = response.json()
                    # API returns paginated response with 'results' key
                    all_nodes = data.get('results', [])
                    
                    # Filter nodes for this simulation only
                    nodes = [n for n in all_nodes if n.get('simulation') == self.simulation_id]
                    
                    # On first check, show all node states for debugging
                    if first_check and nodes:
                        print(f"\n  All nodes in simulation:")
                        for n in nodes:
                            print(f"    • {n.get('name')}: {n.get('state', 'unknown')}")
                        print()
                        first_check = False
                    
                    for node in nodes:
                        if node.get('name') == node_name:
                            state = node.get('state', 'unknown')
                            
                            # Print state if it changed or every 6th check (~60 seconds)
                            if state != last_state or check_count % 6 == 0:
                                print(f"  Node '{node_name}' state: {state}                    ")
                                last_state = state
                            
                            check_count += 1
                            
                            # Accept various ready states that Air might return
                            ready_states = ['READY', 'RUNNING', 'LOADED', 'STARTED', 'BOOTED', 'UP']
                            if state in ready_states or (state and state.upper() in ready_states):
                                self.bcm_node_id = node.get('id')
                                print(f"✓ Node '{node_name}' is ready! (State: {state})")
                                return node
                    
                    # If we get here, node not found in results
                    if not nodes:
                        print(f"  No nodes found yet (simulation might still be initializing)...")
                
                time.sleep(10)
            except Exception as e:
                print(f"  Error checking node status: {e}                    ")
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
                print("  ⏳ Waiting for simulation to fully load...")
            else:
                print(f"  ✗ Failed to start simulation: {response.status_code}")
                print(f"  Response: {response.text}")
        except Exception as e:
            print(f"  Warning: Error starting simulation: {e}")
    
    def wait_for_simulation_loaded(self, timeout=300):
        """
        Wait for simulation to reach LOADED state
        This must complete before SSH can be enabled
        
        Args:
            timeout: Maximum time to wait in seconds
            
        Returns:
            True if loaded, False if timeout
        """
        print("\nWaiting for simulation to finish loading...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                response = requests.get(
                    f"{self.api_base_url}/api/v2/simulations/{self.simulation_id}/",
                    headers=self.headers,
                    timeout=30
                )
                
                if response.status_code == 200:
                    sim_data = response.json()
                    state = sim_data.get('state', 'unknown')
                    print(f"  Simulation state: {state}                    ", end='\r')
                    
                    if state == 'LOADED':
                        print(f"\n✓ Simulation is fully loaded!                    ")
                        return True
                    elif state in ['ERROR', 'FAILED']:
                        print(f"\n✗ Simulation failed to load: {state}")
                        return False
                
                time.sleep(5)
            except Exception as e:
                print(f"  Error checking simulation state: {e}                    ", end='\r')
                time.sleep(5)
        
        print(f"\n⚠ Timeout waiting for simulation to load")
        return False
    
    def enable_ssh_service(self):
        """
        Enable SSH service for the simulation using the Air SDK.
        Creates an SSH service directly on the BCM head node (eth0 port 22).
        
        Note: When using JSON format with oob:false, eth0 can be connected
        to "outbound" for direct external access.
        
        This allows direct SSH access to BCM without going through oob-mgmt-server,
        which avoids password configuration issues with Air-managed nodes.
        
        Returns:
            Service object if successful, None otherwise
        """
        print("\nEnabling SSH service for simulation...")
        print(f"  Creating SSH service on {self.bcm_node_name}:eth0...")
        
        try:
            from air_sdk import AirApi
            
            # Connect to Air SDK
            air = AirApi(
                username=self.username,
                password=self.api_token,
                api_url=self.api_base_url
            )
            
            # Get the simulation object
            sim = air.simulations.get(self.simulation_id)
            
            # Create SSH service directly on BCM head node
            # eth0 is connected to "outbound" for external access
            # (requires JSON topology with oob:false)
            service = sim.create_service(
                name='bcm-ssh',
                interface=f'{self.bcm_node_name}:eth0',
                dest_port=22,
                service_type='ssh'
            )
            
            print(f"  ✓ SSH service created: {service.id}")
            print(f"    Host: {getattr(service, 'host', 'N/A')}")
            print(f"    Port: {getattr(service, 'src_port', 'N/A')}")
            
            return service
            
        except Exception as e:
            print(f"  ✗ Failed to enable SSH service: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def get_ssh_service_info(self):
        """
        Get SSH service details for the BCM head node
        
        Returns:
            dict with 'hostname', 'port', 'username', and 'link' for SSH access
        """
        try:
            # Use v1 API which has complete service information including src_port (external port)
            # Filter by simulation ID to only get services for this simulation
            response = requests.get(
                f"{self.api_base_url}/api/v1/service/",
                headers=self.headers,
                params={'simulation': self.simulation_id},
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                # v1 API can return list directly or dict with results
                services = data if isinstance(data, list) else data.get('results', [])
                
                # Look for SSH service for BCM head node
                for service in services:
                    if (service.get('service_type') == 'ssh' and 
                        service.get('node_name') == self.bcm_node_name):
                        return {
                            'hostname': service.get('host'),
                            'port': service.get('src_port'),  # src_port is the external port
                            'username': 'root',  # BCM uses root, configured via cloud-init
                            'link': service.get('link'),
                            'service_id': service.get('id')
                        }
            
            return None
            
        except Exception as e:
            print(f"  Warning: Could not retrieve SSH service info: {e}")
            return None
    
    def configure_node_passwords_cloudinit(self):
        """
        Configure passwords on nodes using cloud-init via Air SDK (preferred method)
        This sets passwords at boot time, before any SSH attempts
        
        Uses the Air SDK UserConfig API to:
        1. Create a cloud-init user-data script with password configuration
        2. Assign the script to relevant nodes
        
        Returns:
            True if successful, False otherwise
        """
        print("\nConfiguring node passwords via cloud-init...")
        print(f"  Target password: {self.default_password}")
        
        try:
            from air_sdk import AirApi
        except ImportError:
            print("  ⚠ air_sdk not installed. Install with: pip install air-sdk")
            return False
        
        # Ensure cloud-init config exists (auto-generate from template if needed)
        try:
            cloudinit_template_path = self.ensure_cloud_init_config()
        except FileNotFoundError as e:
            print(f"  ⚠ {e}")
            return False
        
        # Read template and substitute password
        cloudinit_template = cloudinit_template_path.read_text()
        cloudinit_content = cloudinit_template.replace('{PASSWORD}', self.default_password)
        
        # Write to temp file for SDK (SDK expects file path or file handle)
        temp_cloudinit = Path('/tmp/air_cloudinit_password.yaml')
        temp_cloudinit.write_text(cloudinit_content)
        
        try:
            # Initialize Air SDK
            print("  Connecting to Air SDK...")
            air = AirApi(
                username=self.username,
                password=self.api_token,
                api_url=self.api_base_url
            )
            
            # Create the cloud-init user-data script
            print("  Creating cloud-init user-data script...")
            userdata_name = f"bcm-password-config-{self.simulation_id[:8]}"
            
            try:
                # Create new UserConfig for cloud-init
                userdata = air.user_configs.create(
                    name=userdata_name,
                    kind='cloud-init-user-data',
                    organization=None,  # Personal config
                    content=str(temp_cloudinit)
                )
                print(f"    ✓ Created UserConfig: {userdata.id}")
            except Exception as e:
                print(f"    ⚠ Error creating UserConfig: {e}")
                # Try to get existing one with same name
                try:
                    configs = air.user_configs.list()
                    for cfg in configs:
                        if cfg.name == userdata_name:
                            userdata = cfg
                            print(f"    ℹ Using existing UserConfig: {userdata.id}")
                            break
                    else:
                        raise Exception("Could not create or find UserConfig")
                except:
                    return False
            
            # Get simulation nodes
            print("  Getting simulation nodes...")
            sim = air.simulations.get(self.simulation_id)
            nodes = air.simulation_nodes.list(simulation=self.simulation_id)
            
            # Apply cloud-init to Ubuntu/Debian nodes that support it
            # Skip switches, PXE boot nodes, and compute nodes
            configured_count = 0
            skipped_nodes = []
            
            for node in nodes:
                node_name = node.name
                
                # Skip switches (they don't support cloud-init)
                if any(skip in node_name.lower() for skip in ['leaf', 'spine', 'switch']):
                    skipped_nodes.append((node_name, 'switch'))
                    continue
                
                # Skip compute nodes (typically PXE boot)
                # Matches: compute0, compute1, compute-01, node01, etc.
                if re.match(r'^(compute|node)\d+', node_name.lower()) or re.match(r'^(compute|node)-\d+', node_name.lower()):
                    skipped_nodes.append((node_name, 'PXE boot (compute node)'))
                    continue
                
                # Skip nodes that are likely PXE boot (check OS if available)
                try:
                    node_os = getattr(node, 'os', '') or ''
                    if 'pxe' in node_os.lower():
                        skipped_nodes.append((node_name, 'PXE boot'))
                        continue
                    if 'cumulus' in node_os.lower():
                        skipped_nodes.append((node_name, 'Cumulus'))
                        continue
                except:
                    pass
                
                print(f"  Applying cloud-init to {node_name}...")
                
                try:
                    # SDK expects a dictionary with 'user_data' key, not keyword args
                    node.set_cloud_init_assignment({'user_data': userdata})
                    print(f"    ✓ Cloud-init assigned to {node_name}")
                    configured_count += 1
                except Exception as e:
                    print(f"    ⚠ Could not assign cloud-init to {node_name}: {e}")
            
            if skipped_nodes:
                print(f"\n  ℹ Skipped nodes (don't support cloud-init):")
                for name, reason in skipped_nodes:
                    print(f"    - {name} ({reason})")
            
            # Clean up temp file
            temp_cloudinit.unlink(missing_ok=True)
            
            if configured_count > 0:
                print(f"\n  ✓ Cloud-init configured on {configured_count} nodes")
                print(f"  ℹ Passwords will be set when nodes boot (or rebuild existing nodes)")
                return True
            else:
                print(f"\n  ⚠ Could not configure cloud-init on any nodes")
                return False
                
        except Exception as e:
            print(f"  ✗ Error configuring cloud-init: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def configure_node_passwords(self, ssh_info):
        """
        Configure passwords on all nodes in the simulation via SSH (fallback method)
        
        Args:
            ssh_info: dict with SSH connection details
        """
        if not ssh_info:
            print("  ⚠ Cannot configure passwords without SSH service info")
            return False
        
        print("\nConfiguring node passwords via SSH...")
        print(f"  Using password: {self.default_password}")
        
        # Create a temporary expect script to handle password changes
        expect_script = f"""#!/usr/bin/expect -f
set timeout 30
set password "{self.default_password}"

# Change password on oob-mgmt-server
spawn ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \\
    -p {ssh_info['port']} ubuntu@{ssh_info['hostname']}

expect {{
    "Current password:" {{
        send "nvidia\\r"
        expect "New password:"
        send "$password\\r"
        expect "Retype new password:"
        send "$password\\r"
        expect eof
    }}
    "$ " {{
        # Already logged in, password already changed
        send "exit\\r"
        expect eof
    }}
    timeout {{
        puts "Timeout connecting to oob-mgmt-server"
        exit 1
    }}
}}

puts "✓ OOB server password configured"
"""
        
        try:
            # Write expect script
            expect_file = Path('/tmp/air_password_config.exp')
            expect_file.write_text(expect_script)
            expect_file.chmod(0o700)
            
            # Run expect script
            result = subprocess.run(
                ['expect', str(expect_file)],
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode == 0:
                print("  ✓ OOB server password configured")
            else:
                print(f"  ⚠ Password configuration had issues (may already be set)")
                print(f"    {result.stdout}")
            
            # Clean up
            expect_file.unlink(missing_ok=True)
            
            return True
            
        except FileNotFoundError:
            print("  ⚠ 'expect' not found. Install with: sudo apt install expect")
            print("  ⚠ Passwords not configured. Use default 'nvidia' and change manually.")
            return False
        except Exception as e:
            print(f"  ⚠ Error configuring passwords: {e}")
            return False
    
    def create_ssh_config(self, ssh_info, simulation_name):
        """
        Create .ssh/config file for easy SSH access to BCM head node
        
        Args:
            ssh_info: dict with 'hostname' and 'port' from get_ssh_service_info()
            simulation_name: Name of the simulation for config file naming
        """
        if not ssh_info or not ssh_info.get('hostname') or not ssh_info.get('port'):
            print("  ⚠ SSH service info not available yet. Config file will not be created.")
            print("  ⚠ You can manually add SSH service in Air UI once simulation is fully loaded.")
            return None
        
        print("\nCreating SSH configuration...")
        
        # Create .ssh directory in project
        project_ssh_dir = Path(__file__).parent / '.ssh'
        project_ssh_dir.mkdir(mode=0o700, exist_ok=True)
        
        # Use simulation name for config file
        config_filename = simulation_name.replace(' ', '-')
        
        # Create config content - direct connection to BCM head node
        config_content = f"""# NVIDIA Air Simulation SSH Configuration
# Simulation: {simulation_name}
# Simulation ID: {self.simulation_id}
# Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}
#
# SSH service is configured directly on the BCM head node.
# Password configured via cloud-init: {self.default_password}

Host air-{self.bcm_node_name}
  HostName {ssh_info['hostname']}
  Port {ssh_info['port']}
  User root
  PreferredAuthentications publickey,password
  IdentityFile {self.ssh_private_key}
  StrictHostKeyChecking no
  UserKnownHostsFile /dev/null

# Alias for convenience
Host bcm
  HostName {ssh_info['hostname']}
  Port {ssh_info['port']}
  User root
  PreferredAuthentications publickey,password
  IdentityFile {self.ssh_private_key}
  StrictHostKeyChecking no
  UserKnownHostsFile /dev/null
"""
        
        # Write config file
        config_file = project_ssh_dir / config_filename
        config_file.write_text(config_content)
        config_file.chmod(0o600)
        
        print(f"  ✓ SSH config created: {config_file}")
        print(f"\n  To access BCM head node:")
        print(f"    ssh -F {config_file} air-{self.bcm_node_name}")
        print(f"    ssh -F {config_file} bcm")
        print(f"\n  Connection details:")
        print(f"    Host: {ssh_info['hostname']}")
        print(f"    Port: {ssh_info['port']}")
        print(f"    User: root")
        print(f"    Password: {self.default_password}")
        
        return config_file
    
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
    
    def execute_ansible_playbook(self, bcm_version, collection_name, ssh_config_file):
        """
        Execute Ansible playbook to install BCM
        
        Args:
            bcm_version: BCM version string (10.x or 11.x)
            collection_name: Ansible Galaxy collection name
            ssh_config_file: Path to SSH config file for ProxyJump access
        """
        print("\n" + "="*60)
        print(f"Installing BCM {bcm_version} via Ansible")
        print("="*60)
        
        # Create temporary inventory file using detected BCM node name
        # Use the SSH config file alias for connection
        inventory_content = f"""[bcm_headnode]
air-{self.bcm_node_name} ansible_user=root ansible_ssh_common_args='-F {ssh_config_file} -o StrictHostKeyChecking=no' ansible_password={self.default_password}

[all:vars]
bcm_version={bcm_version}
bcm_collection={collection_name}
ansible_python_interpreter=/usr/bin/python3
"""
        
        inventory_file = Path('/tmp/bcm_inventory.ini')
        with open(inventory_file, 'w') as f:
            f.write(inventory_content)
        
        print(f"\n✓ Inventory file created")
        print(f"  Using SSH config: {ssh_config_file}")
        print(f"  Target host: air-{self.bcm_node_name}")
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
        print(f"  2. SSH to {self.bcm_node_name} (192.168.200.254)")
        print(f"     Username: root")
        print(f"     Password: {self.default_password}")
        print(f"\nNext Steps:")
        print(f"  1. Disable DHCP on oob-mgmt-server:")
        print(f"     sudo systemctl disable isc-dhcp-server")
        print(f"     sudo service isc-dhcp-server stop")
        print(f"  2. Configure BCM network gateway:")
        print(f"     cmsh")
        print(f"     network; use internalnet; set gateway 192.168.200.1; commit")
        print(f"  3. Add switches and compute nodes to BCM (see README.md)")
        print(f"\nFor BCM GUI access:")
        print(f"  Add a service in Air to expose TCP 8081 on {self.bcm_node_name}")
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
        '--topology', '--dot-file',
        dest='topology_file',
        default='topologies/test-bcm.json',
        help='Path to topology file (.json or .dot). JSON format supports oob:false. (default: topologies/test-bcm.json)'
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
        
        # Prompt for default password
        default_password = deployer.prompt_default_password()
        
        # Prompt for simulation name (or use command-line arg if provided)
        if args.name:
            simulation_name = args.name
            print(f"\nUsing simulation name from command line: {simulation_name}")
        else:
            simulation_name = deployer.prompt_simulation_name()
        
        # Create simulation
        topology_file = Path(args.topology_file)
        if not topology_file.exists():
            print(f"\n✗ Error: Topology file not found: {topology_file}")
            sys.exit(1)
        
        deployer.create_simulation(topology_file, simulation_name)
        
        # Configure passwords via cloud-init (before starting simulation)
        # This is the preferred method - passwords set at boot time
        cloudinit_success = deployer.configure_node_passwords_cloudinit()
        
        # Start the simulation
        deployer.start_simulation()
        
        # Wait for simulation to be fully loaded (required before enabling SSH)
        if not deployer.wait_for_simulation_loaded(timeout=300):
            print("\n✗ Error: Simulation did not load in time")
            return 1
        
        # Now that simulation is loaded, enable SSH service
        deployer.enable_ssh_service()
        
        # Upload ZTP script
        deployer.upload_ztp_script()
        
        # Wait for BCM node to be ready (using detected node name)
        bcm_node = deployer.wait_for_node_ready(deployer.bcm_node_name, timeout=900)
        
        # Get SSH service info and create config file
        print("\n" + "="*60)
        print("Configuring SSH Access")
        print("="*60)
        ssh_info = deployer.get_ssh_service_info()
        
        if not ssh_info:
            print("\n✗ Error: SSH service not available")
            print("   Please enable SSH in Air UI: Services tab > Enable SSH")
            print("   Then run with --skip-ansible to skip to this point")
            return 1
        
        ssh_config_file = deployer.create_ssh_config(ssh_info, simulation_name)
        
        if not ssh_config_file:
            print("\n✗ Error: Could not create SSH config")
            return 1
        
        # If cloud-init didn't work, fallback to SSH-based password configuration
        if not cloudinit_success:
            print("\n⚠ Cloud-init configuration failed, using SSH fallback...")
            deployer.configure_node_passwords(ssh_info)
        else:
            print("\n✓ Passwords configured via cloud-init (set at boot time)")
        
        if not args.skip_ansible:
            # Execute Ansible playbook
            deployer.execute_ansible_playbook(bcm_version, collection_name, ssh_config_file)
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

