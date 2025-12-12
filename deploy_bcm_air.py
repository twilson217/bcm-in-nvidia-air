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


class ProgressTracker:
    """Track deployment progress for resume functionality"""
    
    STEPS = [
        'init',
        'bcm_version_selected',
        'password_configured',
        'simulation_name_set',
        'simulation_created',
        'cloudinit_configured',
        'simulation_started',
        'simulation_loaded',
        'ssh_enabled',
        'node_ready',
        'ssh_configured',
        'bcm_installed',
        'completed'
    ]
    
    def __init__(self, log_dir=None):
        self.log_dir = Path(log_dir) if log_dir else Path(__file__).parent / '.logs'
        self.progress_file = self.log_dir / 'progress.json'
        self.data = self._load()
    
    def _load(self):
        """Load progress from file"""
        if self.progress_file.exists():
            try:
                with open(self.progress_file, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}
    
    def _save(self):
        """Save progress to file"""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with open(self.progress_file, 'w') as f:
            json.dump(self.data, f, indent=2, default=str)
    
    def get_last_step(self):
        """Get the last completed step"""
        return self.data.get('last_step', None)
    
    def get_step_index(self, step):
        """Get index of a step"""
        try:
            return self.STEPS.index(step)
        except ValueError:
            return -1
    
    def is_step_completed(self, step):
        """Check if a step has been completed"""
        last_step = self.get_last_step()
        if not last_step:
            return False
        return self.get_step_index(step) <= self.get_step_index(last_step)
    
    def complete_step(self, step, **kwargs):
        """Mark a step as completed and store any associated data"""
        self.data['last_step'] = step
        self.data['last_updated'] = datetime.now().isoformat()
        for key, value in kwargs.items():
            self.data[key] = value
        self._save()
    
    def get(self, key, default=None):
        """Get a stored value"""
        return self.data.get(key, default)
    
    def clear(self):
        """Clear all progress"""
        self.data = {}
        if self.progress_file.exists():
            self.progress_file.unlink()
    
    def show_status(self):
        """Display current progress status"""
        last_step = self.get_last_step()
        if not last_step:
            print("  No previous progress found")
            return
        
        print(f"  Last completed step: {last_step}")
        print(f"  Last updated: {self.data.get('last_updated', 'unknown')}")
        
        if self.data.get('simulation_id'):
            print(f"  Simulation ID: {self.data.get('simulation_id')}")
        if self.data.get('simulation_name'):
            print(f"  Simulation name: {self.data.get('simulation_name')}")
        if self.data.get('bcm_version'):
            print(f"  BCM version: {self.data.get('bcm_version')}")


class AirBCMDeployer:
    """Automate BCM deployment on NVIDIA Air"""
    
    def __init__(self, api_base_url="https://air.nvidia.com", api_token=None, username=None, 
                 non_interactive=False, progress_tracker=None):
        """
        Initialize the deployer
        
        Args:
            api_base_url: NVIDIA Air base URL (without /api/vX)
            api_token: API authentication token
            username: Air account username/email
            non_interactive: If True, accept defaults for all prompts
            progress_tracker: ProgressTracker instance for resume functionality
        """
        self.non_interactive = non_interactive
        self.progress = progress_tracker or ProgressTracker()
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
        
        # Load BCM configuration from environment
        self.bcm_product_key = os.getenv('BCM_PRODUCT_KEY', '')
        self.bcm_admin_email = os.getenv('BCM_ADMIN_EMAIL', self.username)
        
        # Validate SSH keys exist
        self._validate_ssh_keys()
        
        # Ensure cloud-init file exists (auto-generate from template if needed)
        self._ensure_cloudinit_config()
        # Note: BCM config is generated later, after password prompt
        
        # Authenticate and get JWT token
        self.jwt_token = self._authenticate()
        
        self.headers = {
            'Authorization': f'Bearer {self.jwt_token}',
            'Content-Type': 'application/json'
        }
        self.simulation_id = None
        self.bcm_node_id = None
    
    def _validate_ssh_keys(self):
        """Validate that SSH key files exist"""
        if not os.path.exists(self.ssh_private_key):
            print(f"\n⚠ Warning: SSH private key not found: {self.ssh_private_key}")
            print(f"  SSH connections may fail. Update SSH_PRIVATE_KEY in .env")
        
        if not os.path.exists(self.ssh_public_key):
            print(f"\n✗ Error: SSH public key not found: {self.ssh_public_key}")
            print(f"\nPlease update SSH_PUBLIC_KEY in .env to point to your public key.")
            print(f"Common locations:")
            print(f"  ~/.ssh/id_rsa.pub")
            print(f"  ~/.ssh/id_ed25519.pub")
            raise FileNotFoundError(f"SSH public key not found: {self.ssh_public_key}")
    
    def _ensure_cloudinit_config(self):
        """
        Ensure cloud-init-password.yaml exists.
        If not, auto-generate it from the template using the user's SSH public key.
        """
        cloudinit_file = Path(__file__).parent / 'cloud-init-password.yaml'
        template_file = Path(__file__).parent / 'sample-configs' / 'cloud-init-password.yaml.example'
        
        if cloudinit_file.exists():
            return  # Already exists
        
        if not template_file.exists():
            print(f"\n✗ Error: Cloud-init template not found: {template_file}")
            raise FileNotFoundError("cloud-init-password.yaml.example not found")
        
        # Read the user's public key
        try:
            with open(self.ssh_public_key, 'r') as f:
                public_key = f.read().strip()
        except Exception as e:
            print(f"\n✗ Error reading SSH public key: {e}")
            raise
        
        # Read template and replace placeholder with actual key
        template_content = template_file.read_text()
        cloudinit_content = template_content.replace('YOUR_SSH_PUBLIC_KEY_HERE', public_key)
        
        # Write the cloud-init file
        cloudinit_file.write_text(cloudinit_content)
        print(f"\n✓ Auto-generated cloud-init-password.yaml with your SSH key")
        print(f"  Public key: {self.ssh_public_key}")
    
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
        
    def scan_available_isos(self):
        """
        Scan .iso/ directory and return available BCM ISOs with version info.
        
        Returns:
            dict: {
                '10': [{'version': '10.30.0', 'file': Path, 'size_gb': float}, ...],
                '11': [{'version': '11.30.0', 'file': Path, 'size_gb': float}, ...]
            }
        """
        iso_dir = Path(__file__).parent / '.iso'
        result = {'10': [], '11': []}
        
        if not iso_dir.exists():
            return result
        
        # Pattern to extract version: bcm-10.30.0-xxx.iso or bcm-11.0-xxx.iso
        import re
        version_pattern = re.compile(r'bcm-?(10|11)\.?(\d+)?\.?(\d+)?', re.IGNORECASE)
        
        for iso_file in iso_dir.glob('*.iso'):
            name_lower = iso_file.name.lower()
            match = version_pattern.search(name_lower)
            
            if match:
                major = match.group(1)  # '10' or '11'
                minor = match.group(2) or '0'  # e.g., '30' or '0'
                patch = match.group(3) or '0'  # e.g., '0'
                
                version = f"{major}.{minor}.{patch}"
                size_gb = iso_file.stat().st_size / (1024**3)
                
                result[major].append({
                    'version': version,
                    'file': iso_file,
                    'size_gb': size_gb,
                    'filename': iso_file.name
                })
        
        # Sort each list by version (newest first)
        for major in result:
            result[major].sort(key=lambda x: [int(p) for p in x['version'].split('.')], reverse=True)
        
        return result
    
    def prompt_bcm_version(self, requested_version=None):
        """
        Prompt user for BCM version selection.
        
        Args:
            requested_version: Optional specific version requested via --bcm-version
                               Can be '10', '11', '10.30.0', '11.30.0', etc.
        
        Returns:
            tuple: (version_string, collection_name, iso_path)
        """
        print("\n" + "="*60)
        print("BCM Version Selection")
        print("="*60)
        
        # Scan available ISOs
        available = self.scan_available_isos()
        bcm10_isos = available['10']
        bcm11_isos = available['11']
        
        # Show available ISOs
        print("\nAvailable BCM ISOs:")
        all_options = []
        
        if bcm10_isos:
            print("  BCM 10:")
            for iso in bcm10_isos:
                all_options.append(('10', iso))
                print(f"    - {iso['version']}: {iso['filename']} ({iso['size_gb']:.2f} GB)")
        else:
            print("  BCM 10: (no ISOs found)")
        
        if bcm11_isos:
            print("  BCM 11:")
            for iso in bcm11_isos:
                all_options.append(('11', iso))
                print(f"    - {iso['version']}: {iso['filename']} ({iso['size_gb']:.2f} GB)")
        else:
            print("  BCM 11: (no ISOs found)")
        
        if not all_options:
            print("\n✗ No BCM ISOs found in .iso/ directory")
            print("  Download from: https://customer.brightcomputing.com/download-iso")
            return None, None, None
        
        # If a specific version was requested
        if requested_version:
            return self._resolve_requested_version(requested_version, available)
        
        # Non-interactive mode
        if self.non_interactive:
            # Default to BCM 10 if available, else BCM 11
            if len(bcm10_isos) == 1:
                iso = bcm10_isos[0]
                print(f"\n  [non-interactive] Using: BCM {iso['version']}")
                return iso['version'], 'brightcomputing.installer100', iso['file']
            elif len(bcm10_isos) > 1:
                print("\n✗ Multiple BCM 10 ISOs found. In non-interactive mode, use:")
                print(f"   --bcm-version {bcm10_isos[0]['version']}")
                for iso in bcm10_isos:
                    print(f"   --bcm-version {iso['version']}")
                return None, None, None
            elif len(bcm11_isos) == 1:
                iso = bcm11_isos[0]
                print(f"\n  [non-interactive] Using: BCM {iso['version']}")
                return iso['version'], 'brightcomputing.installer110', iso['file']
            elif len(bcm11_isos) > 1:
                print("\n✗ Multiple BCM 11 ISOs found. In non-interactive mode, use:")
                for iso in bcm11_isos:
                    print(f"   --bcm-version {iso['version']}")
                return None, None, None
        
        # Interactive mode - let user choose
        print("\nSelect BCM version to install:")
        for i, (major, iso) in enumerate(all_options, 1):
            print(f"  {i}) BCM {iso['version']} ({iso['filename']})")
        
        default_choice = 1
        while True:
            choice = input(f"Enter your choice [default: {default_choice}]: ").strip()
            if choice == '':
                choice = default_choice
            else:
                try:
                    choice = int(choice)
                except ValueError:
                    print("Invalid choice. Enter a number.")
                    continue
            
            if 1 <= choice <= len(all_options):
                major, iso = all_options[choice - 1]
                collection = f'brightcomputing.installer{major}0'
                print(f"\n✓ Selected: BCM {iso['version']}")
                return iso['version'], collection, iso['file']
            else:
                print(f"Invalid choice. Enter 1-{len(all_options)}.")
    
    def _resolve_requested_version(self, requested_version, available):
        """
        Resolve a requested version string to a specific ISO.
        
        Args:
            requested_version: '10', '11', '10.30.0', '11.30.0', etc.
            available: Dict from scan_available_isos()
        
        Returns:
            tuple: (version_string, collection_name, iso_path) or (None, None, None)
        """
        # Determine major version
        if requested_version.startswith('10'):
            major = '10'
            isos = available['10']
            collection = 'brightcomputing.installer100'
        elif requested_version.startswith('11'):
            major = '11'
            isos = available['11']
            collection = 'brightcomputing.installer110'
        else:
            print(f"\n✗ Invalid BCM version: {requested_version}")
            print("  Version must start with 10 or 11 (e.g., 10, 11, 10.30.0, 11.30.0)")
            return None, None, None
        
        if not isos:
            print(f"\n✗ No BCM {major} ISOs found in .iso/ directory")
            return None, None, None
        
        # If just major version requested (10 or 11)
        if requested_version in ('10', '11'):
            if len(isos) == 1:
                iso = isos[0]
                print(f"\n✓ Using BCM {iso['version']} ({iso['filename']})")
                return iso['version'], collection, iso['file']
            else:
                print(f"\n✗ Multiple BCM {major} ISOs found. Please specify exact version:")
                for iso in isos:
                    print(f"   --bcm-version {iso['version']}")
                return None, None, None
        
        # Specific version requested - find exact match
        for iso in isos:
            if iso['version'] == requested_version:
                print(f"\n✓ Using BCM {iso['version']} ({iso['filename']})")
                return iso['version'], collection, iso['file']
        
        # Try partial match (e.g., 10.30 matches 10.30.0)
        for iso in isos:
            if iso['version'].startswith(requested_version):
                print(f"\n✓ Using BCM {iso['version']} ({iso['filename']})")
                return iso['version'], collection, iso['file']
        
        print(f"\n✗ No ISO found matching version {requested_version}")
        print(f"  Available BCM {major} versions:")
        for iso in isos:
            print(f"   - {iso['version']}: {iso['filename']}")
        return None, None, None
    
    def prompt_default_password(self):
        """Prompt user for default password for nodes"""
        print("\n" + "="*60)
        print("Default Password Configuration")
        print("="*60)
        print("\nSet the default password for all nodes in the simulation.")
        print(f"Default: Nvidia1234!")
        
        # Non-interactive mode: use default
        if self.non_interactive:
            self.default_password = "Nvidia1234!"
            print("  [non-interactive] Using default password")
            return self.default_password
        
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
        template_path = Path(__file__).parent / 'sample-configs' / 'cloud-init-password.yaml.example'
        
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
    
    def detect_bcm_outbound_interface(self, topology_data):
        """
        Detect which interface on the BCM node connects to "outbound".
        This interface will be used for SSH service.
        
        Args:
            topology_data: Parsed JSON topology data
            
        Returns:
            Interface name (e.g., 'eth0', 'eth4') or None if not found
        """
        links = topology_data.get('content', {}).get('links', [])
        
        for link in links:
            if len(link) != 2:
                continue
            
            # Check if this link connects BCM node to "outbound"
            endpoint1 = link[0]
            endpoint2 = link[1]
            
            # endpoint can be a dict {"interface": "eth4", "node": "bcm-01"} or string "outbound"
            if isinstance(endpoint1, dict) and endpoint2 == "outbound":
                if endpoint1.get('node') == self.bcm_node_name:
                    iface = endpoint1.get('interface')
                    print(f"  ✓ BCM outbound interface detected: {self.bcm_node_name}:{iface}")
                    return iface
            
            if isinstance(endpoint2, dict) and endpoint1 == "outbound":
                if endpoint2.get('node') == self.bcm_node_name:
                    iface = endpoint2.get('interface')
                    print(f"  ✓ BCM outbound interface detected: {self.bcm_node_name}:{iface}")
                    return iface
        
        print(f"  ⚠ No outbound interface found for {self.bcm_node_name}")
        return None
    
    def detect_bcm_management_interface(self, topology_data):
        """
        Detect which interface on the BCM node connects to "oob-mgmt-switch".
        This interface will be configured as the BCM management network (192.168.200.x).
        
        Args:
            topology_data: Parsed JSON topology data
            
        Returns:
            Interface name (e.g., 'eth0', 'eth1') or None if not found
        """
        links = topology_data.get('content', {}).get('links', [])
        
        for link in links:
            if len(link) != 2:
                continue
            
            endpoint1 = link[0]
            endpoint2 = link[1]
            
            # Check if BCM node connects to oob-mgmt-switch
            if isinstance(endpoint1, dict) and isinstance(endpoint2, dict):
                if endpoint1.get('node') == self.bcm_node_name and endpoint2.get('node') == 'oob-mgmt-switch':
                    iface = endpoint1.get('interface')
                    print(f"  ✓ BCM management interface detected: {self.bcm_node_name}:{iface} → oob-mgmt-switch")
                    return iface
                if endpoint2.get('node') == self.bcm_node_name and endpoint1.get('node') == 'oob-mgmt-switch':
                    iface = endpoint2.get('interface')
                    print(f"  ✓ BCM management interface detected: {self.bcm_node_name}:{iface} → oob-mgmt-switch")
                    return iface
        
        print(f"  ⚠ No oob-mgmt-switch connection found for {self.bcm_node_name}")
        return None
    
    def _get_topology_nodes(self):
        """
        Get node configurations from the loaded topology.
        Returns dict of node_name -> node_config, or None if not available.
        """
        return getattr(self, '_topology_nodes_cache', None)
    
    def _cache_topology_nodes(self, topology_data):
        """Cache topology node configurations for later use"""
        self._topology_nodes_cache = topology_data.get('content', {}).get('nodes', {})
    
    def _is_pxe_boot_node(self, node_name, topo_node):
        """
        Determine if a node is configured for PXE boot (as a client).
        
        Detection criteria (in order of reliability):
        1. "boot": "network" - explicitly set to network boot
        2. "os" contains "pxe" - OS is a PXE boot image
        
        Note: "pxehost" indicates if the node is a PXE SERVER, not client.
        """
        # Check for explicit network boot setting
        if topo_node.get('boot') == 'network':
            return True
        
        # Check for PXE OS
        node_os = topo_node.get('os', '')
        if isinstance(node_os, str) and 'pxe' in node_os.lower():
            return True
        
        return False
    
    def _is_switch_node(self, node_name, topo_node):
        """
        Determine if a node is a network switch.
        
        Detection criteria:
        1. function attribute (leaf, spine, switch)
        2. OS contains cumulus, sonic, or switch
        3. Name patterns (leaf, spine, switch, tor, agg)
        """
        # Check function attribute
        function = topo_node.get('function', '').lower()
        if function in ['leaf', 'spine', 'switch', 'oob-switch']:
            return True
        
        # Check OS for switch indicators
        node_os = topo_node.get('os', '').lower()
        if any(x in node_os for x in ['cumulus', 'sonic', 'switch']):
            return True
        
        # Check name patterns as fallback
        name_lower = node_name.lower()
        if any(x in name_lower for x in ['leaf', 'spine', 'switch', 'tor', 'agg']):
            return True
        
        return False
    
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
        
        # Non-interactive mode: use default
        if self.non_interactive:
            print("  [non-interactive] Using default name")
            return default_name
        
        print("Press Enter to use default, or type a custom name:")
        
        user_input = input("> ").strip()
        
        if user_input:
            return user_input
        else:
            print(f"Using default name: {default_name}")
            return default_name
    
    def create_simulation(self, topology_file_path, simulation_name):
        """
        Create a simulation from a JSON topology file.
        
        Args:
            topology_file_path: Path to the JSON topology file
            simulation_name: Name for the simulation
        
        Returns:
            Simulation ID
        """
        print("\n" + "="*60)
        print("Creating NVIDIA Air Simulation")
        print("="*60)
        
        topology_path = Path(topology_file_path)
        file_ext = topology_path.suffix.lower()
        
        if file_ext != '.json':
            raise Exception(
                f"Only JSON topology files are supported.\n"
                "Create custom topologies in NVIDIA Air web UI and export to JSON.\n"
                "See topologies/topology-design.md for requirements."
            )
        
        # JSON format - read and parse
        with open(topology_path, 'r') as f:
            topology_data = json.load(f)
        
        # Detect BCM node from JSON topology
        nodes = topology_data.get('content', {}).get('nodes', {})
        bcm_node = self.detect_bcm_nodes_json(nodes)
        print(f"\n  ✓ Detected BCM node: {bcm_node}")
        
        # Cache topology nodes for later PXE/switch detection
        self._cache_topology_nodes(topology_data)
        
        # Detect which interface connects to outbound (for SSH service)
        self.bcm_outbound_interface = self.detect_bcm_outbound_interface(topology_data)
        if not self.bcm_outbound_interface:
            raise Exception(
                f"BCM node '{bcm_node}' must have an interface connected to 'outbound' for SSH access.\n"
                "See topologies/topology-design.md for requirements."
            )
        
        # Detect which interface connects to oob-mgmt-switch (for BCM management network)
        self.bcm_management_interface = self.detect_bcm_management_interface(topology_data)
        if not self.bcm_management_interface:
            print(f"  ℹ No oob-mgmt-switch found - using eth0 as default management interface")
            self.bcm_management_interface = 'eth0'
        
        print(f"\nCreating simulation from JSON file: {simulation_name}")
        
        # Override title with our simulation name
        topology_data['title'] = simulation_name
        payload = topology_data
        content_size = len(json.dumps(topology_data))
        
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
        
        # Use Air SDK for reliable node listing (same method that works for cloud-init)
        try:
            from air_sdk import AirApi
            air = AirApi(api_url=self.api_base_url, bearer_token=self.jwt_token)
            sim = air.simulations.get(self.simulation_id)
            use_sdk = True
            print(f"  Using Air SDK for node status...")
        except Exception as e:
            print(f"  SDK unavailable ({e}), using REST API...")
            use_sdk = False
        
        while time.time() - start_time < timeout:
            try:
                nodes = []
                
                if use_sdk:
                    # Use SDK to get nodes - this is what worked for cloud-init
                    try:
                        sdk_nodes = list(sim.nodes)
                        for n in sdk_nodes:
                            nodes.append({
                                'name': n.name,
                                'state': getattr(n, 'state', 'unknown'),
                                'id': str(n.id) if hasattr(n, 'id') else None
                            })
                    except Exception as e:
                        print(f"  SDK error: {e}, falling back to REST API")
                        use_sdk = False
                
                if not use_sdk:
                    # Fallback to REST API
                    response = requests.get(
                        f"{self.api_base_url}/api/v2/simulations/{self.simulation_id}/nodes/",
                        headers=self.headers,
                        timeout=30
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        if isinstance(data, list):
                            nodes = data
                        else:
                            nodes = data.get('results', [])
                
                # On first check, show all node states for debugging
                if first_check:
                    print(f"\n  All nodes in simulation ({len(nodes)} found):")
                    if nodes:
                        for n in nodes:
                            name = n.get('name') if isinstance(n, dict) else getattr(n, 'name', 'unknown')
                            state = n.get('state', 'unknown') if isinstance(n, dict) else getattr(n, 'state', 'unknown')
                            print(f"    • {name}: {state}")
                    else:
                        print(f"    (no nodes returned)")
                    print()
                    first_check = False
                
                # Check if target node is in the list
                target_node = None
                for node in nodes:
                    name = node.get('name') if isinstance(node, dict) else getattr(node, 'name', None)
                    if name == node_name:
                        target_node = node
                        break
                
                if target_node:
                    state = target_node.get('state', 'unknown') if isinstance(target_node, dict) else getattr(target_node, 'state', 'unknown')
                    
                    # Print state if it changed or every 6th check (~60 seconds)
                    if state != last_state or check_count % 6 == 0:
                        print(f"  Node '{node_name}' state: {state}                    ")
                        last_state = state
                    
                    check_count += 1
                    
                    # Accept various ready states that Air might return
                    ready_states = ['READY', 'RUNNING', 'LOADED', 'STARTED', 'BOOTED', 'UP']
                    if state in ready_states or (state and str(state).upper() in ready_states):
                        node_id = target_node.get('id') if isinstance(target_node, dict) else getattr(target_node, 'id', None)
                        self.bcm_node_id = str(node_id) if node_id else None
                        print(f"✓ Node '{node_name}' is ready! (State: {state})")
                        return target_node
                else:
                    # Node not found in results - print message periodically
                    if check_count % 6 == 0:
                        print(f"  Node '{node_name}' not found in API response (checking...)                    ")
                    check_count += 1
                
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
        Creates an SSH service on the BCM head node's outbound interface.
        
        The outbound interface is detected from the topology during simulation creation.
        This interface must be connected to "outbound" in the topology for external access.
        
        Returns:
            Service object if successful, None otherwise
        """
        # Use detected outbound interface, or fall back to eth0
        interface = getattr(self, 'bcm_outbound_interface', 'eth0')
        
        print("\nEnabling SSH service for simulation...")
        print(f"  Creating SSH service on {self.bcm_node_name}:{interface}...")
        
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
            
            # Create SSH service on BCM head node's outbound interface
            # This interface must be connected to "outbound" in the topology
            service = sim.create_service(
                name='bcm-ssh',
                interface=f'{self.bcm_node_name}:{interface}',
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
            # Skip switches and PXE boot nodes (detected by settings, not names)
            configured_count = 0
            skipped_nodes = []
            
            # Load topology to get node configurations
            topology_nodes = self._get_topology_nodes() or {}
            
            for node in nodes:
                node_name = node.name
                topo_node = topology_nodes.get(node_name, {})
                
                # Check if node is a switch (by OS or function)
                if self._is_switch_node(node_name, topo_node):
                    skipped_nodes.append((node_name, 'switch'))
                    continue
                
                # Check if node is a PXE boot client (by boot setting or OS)
                if self._is_pxe_boot_node(node_name, topo_node):
                    skipped_nodes.append((node_name, 'PXE boot'))
                    continue
                
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
  User ubuntu
  PreferredAuthentications publickey,password
  IdentityFile {self.ssh_private_key}
  StrictHostKeyChecking no
  UserKnownHostsFile /dev/null

# Alias for convenience
Host bcm
  HostName {ssh_info['hostname']}
  Port {ssh_info['port']}
  User ubuntu
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
    
    def find_bcm_iso(self, bcm_version):
        """
        Find BCM ISO file in ./iso/ directory
        
        Args:
            bcm_version: BCM version string (10.x or 11.x)
            
        Returns:
            Path to ISO file, or None if not found
        """
        iso_dir = Path(__file__).parent / '.iso'
        
        if not iso_dir.exists():
            print(f"\n⚠ ISO directory not found: {iso_dir}")
            print(f"  Please create ./.iso/ and place your BCM ISO there")
            return None
        
        # Extract major version number (10 or 11)
        major_version = bcm_version.split('.')[0]
        
        # Look for ISO files matching the version
        patterns = [
            f'bcm-{major_version}*.iso',
            f'BCM-{major_version}*.iso',
            f'*bcm*{major_version}*.iso',
        ]
        
        for pattern in patterns:
            matches = list(iso_dir.glob(pattern))
            if matches:
                # Return the first match (or most recent if multiple)
                iso_file = sorted(matches, key=lambda p: p.stat().st_mtime, reverse=True)[0]
                print(f"\n✓ Found BCM ISO: {iso_file.name}")
                print(f"  Size: {iso_file.stat().st_size / (1024**3):.2f} GB")
                return iso_file
        
        # If no version-specific ISO, look for any ISO
        all_isos = list(iso_dir.glob('*.iso'))
        if all_isos:
            iso_file = sorted(all_isos, key=lambda p: p.stat().st_mtime, reverse=True)[0]
            print(f"\n⚠ Using ISO (version not verified): {iso_file.name}")
            return iso_file
        
        print(f"\n✗ No BCM ISO found in {iso_dir}")
        print(f"  Download your BCM ISO from: https://customer.brightcomputing.com/download-iso")
        print(f"  Create directory: mkdir .iso")
        print(f"  Place the ISO file in: ./.iso/")
        return None
    
    def upload_iso_to_bcm(self, iso_path, ssh_config_file):
        """
        Upload BCM ISO to the head node via rsync
        
        Args:
            iso_path: Local path to ISO file
            ssh_config_file: Path to SSH config file
            
        Returns:
            True if successful, False otherwise
        """
        print(f"\n📦 Uploading BCM ISO to head node...")
        print(f"  Source: {iso_path}")
        print(f"  This may take 10-20 minutes depending on connection speed...")
        
        # Wait a moment for SSH service to stabilize
        print(f"  Waiting for SSH service to stabilize...")
        time.sleep(10)
        
        # Use rsync for reliable large file transfer
        # Upload to /home/ubuntu/ since we connect as ubuntu user
        ssh_cmd = f"ssh -F {ssh_config_file} -o StrictHostKeyChecking=no"
        remote_path = f"air-{self.bcm_node_name}:/home/ubuntu/bcm.iso"
        
        cmd = [
            'rsync',
            '-avz',
            '--partial',      # Keep partial files on interrupt (enables resume)
            '--progress',
            '-e', ssh_cmd,
            str(iso_path),
            remote_path
        ]
        
        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=False,
                text=True
            )
            print(f"\n✓ ISO uploaded successfully")
            return True
        except subprocess.CalledProcessError as e:
            print(f"\n✗ ISO upload failed: {e}")
            return False
        except FileNotFoundError:
            print(f"\n✗ rsync not found. Please install rsync:")
            print(f"    sudo apt-get install rsync")
            return False
    
    def upload_install_script(self, bcm_version, ssh_config_file):
        """
        Upload and prepare bcm_install.sh on the head node
        
        Args:
            bcm_version: BCM version string (10.x or 11.x)
            ssh_config_file: Path to SSH config file
            
        Returns:
            True if successful, False otherwise
        """
        print(f"\n📜 Uploading BCM installation script...")
        
        # Read the template script
        script_template = Path(__file__).parent / 'scripts' / 'bcm_install.sh'
        if not script_template.exists():
            print(f"\n✗ Script template not found: {script_template}")
            return False
        
        script_content = script_template.read_text()
        
        # Extract major version (10 or 11)
        major_version = bcm_version.split('.')[0]
        
        # Replace placeholders (using __NAME__ format to avoid bash variable conflicts)
        script_content = script_content.replace('__PASSWORD__', self.default_password)
        script_content = script_content.replace('__PRODUCT_KEY__', self.bcm_product_key)
        script_content = script_content.replace('__BCM_VERSION__', major_version)
        script_content = script_content.replace('__ADMIN_EMAIL__', self.bcm_admin_email)
        script_content = script_content.replace('__EXTERNAL_INTERFACE__', self.bcm_outbound_interface)
        script_content = script_content.replace('__MANAGEMENT_INTERFACE__', self.bcm_management_interface)
        
        # Write to temp file
        temp_script = Path('/tmp/bcm_install.sh')
        temp_script.write_text(script_content)
        temp_script.chmod(0o755)
        
        # Upload via scp
        ssh_cmd = f"-F {ssh_config_file} -o StrictHostKeyChecking=no"
        remote_path = f"air-{self.bcm_node_name}:/home/ubuntu/bcm_install.sh"
        
        cmd = [
            'scp',
            '-F', ssh_config_file,
            '-o', 'StrictHostKeyChecking=no',
            str(temp_script),
            remote_path
        ]
        
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            print(f"  ✓ Script uploaded")
            
            # Make executable on remote
            ssh_make_exec = [
                'ssh',
                '-F', ssh_config_file,
                '-o', 'StrictHostKeyChecking=no',
                f'air-{self.bcm_node_name}',
                'chmod +x /home/ubuntu/bcm_install.sh'
            ]
            subprocess.run(ssh_make_exec, check=True, capture_output=True)
            
            return True
        except subprocess.CalledProcessError as e:
            print(f"\n✗ Script upload failed: {e}")
            return False
    
    def execute_bcm_install(self, ssh_config_file):
        """
        Execute BCM installation script on the head node
        
        Args:
            ssh_config_file: Path to SSH config file
            
        Returns:
            True if successful, False otherwise
        """
        print(f"\n🚀 Starting BCM installation on head node...")
        print(f"  This will take 30-45 minutes.")
        print(f"  You can monitor progress in a separate terminal:")
        print(f"    ssh -F {ssh_config_file} air-{self.bcm_node_name} 'tail -f /home/ubuntu/ansible_bcm_install.log'")
        print("")
        
        # Execute the install script via SSH
        # Use sudo since we're connecting as ubuntu user
        cmd = [
            'ssh',
            '-F', ssh_config_file,
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'ServerAliveInterval=60',
            '-o', 'ServerAliveCountMax=30',
            f'air-{self.bcm_node_name}',
            'sudo /home/ubuntu/bcm_install.sh'
        ]
        
        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=False,
                text=True
            )
            print("\n✓ BCM installation completed successfully!")
            return True
        except subprocess.CalledProcessError as e:
            print(f"\n✗ BCM installation failed with exit code {e.returncode}")
            print(f"  Check logs: ssh -F {ssh_config_file} air-{self.bcm_node_name} 'cat /home/ubuntu/ansible_bcm_install.log'")
            return False
    
    def install_bcm(self, bcm_version, ssh_config_file, iso_path=None):
        """
        Main BCM installation method - uploads ISO and runs installation
        
        Args:
            bcm_version: BCM version string (e.g., '10.30.0', '11.30.0')
            ssh_config_file: Path to SSH config file
            iso_path: Path to BCM ISO file (optional, will search if not provided)
        """
        print("\n" + "="*60)
        print(f"Installing BCM {bcm_version}")
        print("="*60)
        
        # Validate product key
        if not self.bcm_product_key or self.bcm_product_key == 'your_product_key_here':
            print("\n✗ BCM_PRODUCT_KEY not configured in .env")
            print("  Please add your BCM license key to .env:")
            print("    BCM_PRODUCT_KEY=your_key_here")
            raise ValueError("BCM product key not configured")
        
        # Step 1: Find ISO if not provided
        if not iso_path:
            iso_path = self.find_bcm_iso(bcm_version)
        if not iso_path:
            raise FileNotFoundError("BCM ISO not found")
        
        # Step 2: Upload ISO
        if not self.upload_iso_to_bcm(iso_path, ssh_config_file):
            raise RuntimeError("Failed to upload BCM ISO")
        
        # Step 3: Upload install script
        if not self.upload_install_script(bcm_version, ssh_config_file):
            raise RuntimeError("Failed to upload installation script")
        
        # Step 4: Execute installation
        if not self.execute_bcm_install(ssh_config_file):
            raise RuntimeError("BCM installation failed")
    
    def print_summary(self, bcm_version, ssh_config_file=None):
        """Print deployment summary and next steps"""
        print("\n" + "="*60)
        print("Deployment Complete!")
        print("="*60)
        
        print(f"\nBCM {bcm_version} has been deployed on NVIDIA Air")
        print(f"\nSimulation ID: {self.simulation_id}")
        
        if ssh_config_file:
            print(f"\nSSH Access:")
            print(f"  ssh -F {ssh_config_file} air-{self.bcm_node_name}")
        
        print(f"\nBCM Access:")
        print(f"  Hostname: {self.bcm_node_name}")
        print(f"  Internal IP: 192.168.200.254")
        print(f"  Username: root")
        print(f"  Password: {self.default_password}")
        
        print(f"\nBCM CLI:")
        print(f"  cmsh                    # Enter BCM shell")
        print(f"  device list             # List managed devices")
        
        print(f"\nBCM GUI:")
        print(f"  Add a service in Air to expose TCP 8081 on {self.bcm_node_name}")
        print(f"  Access at: https://<worker_url>:<port>/base-view")
        
        print(f"\nInstallation Logs:")
        if ssh_config_file:
            print(f"  ssh -F {ssh_config_file} air-{self.bcm_node_name} 'cat /home/ubuntu/ansible_bcm_install.log'")
        else:
            print(f"  /home/ubuntu/ansible_bcm_install.log on {self.bcm_node_name}")
        
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
  
  # Non-interactive deployment (accept all defaults: BCM 10, Nvidia1234!, auto name)
  python deploy_bcm_air.py --non-interactive
  python deploy_bcm_air.py -y
  
  # Non-interactive with BCM 11
  python deploy_bcm_air.py -y --bcm-version 11
  
  # Resume a failed/interrupted deployment
  python deploy_bcm_air.py --resume
  
  # Clear progress and start fresh
  python deploy_bcm_air.py --clear-progress
  
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
        '--topology',
        dest='topology_file',
        default='topologies/default.json',
        help='Path to JSON topology file. Create custom topologies in NVIDIA Air web UI and export to JSON. (default: topologies/default.json)'
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
    parser.add_argument(
        '--bcm-version',
        help='BCM version to install. Can be major version (10, 11) or specific release (10.30.0, 11.30.0). If multiple ISOs exist for a major version, specific release is required in non-interactive mode.'
    )
    parser.add_argument(
        '--non-interactive', '-y',
        action='store_true',
        help='Accept defaults for all prompts (BCM 10, Nvidia1234!, auto-generated name)'
    )
    parser.add_argument(
        '--resume',
        action='store_true',
        help='Resume from last checkpoint (uses .logs/progress.json)'
    )
    parser.add_argument(
        '--clear-progress',
        action='store_true',
        help='Clear saved progress and start fresh'
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
        # Initialize progress tracker
        progress = ProgressTracker()
        
        # Handle --clear-progress
        if args.clear_progress:
            progress.clear()
            print("\n✓ Progress cleared")
            if not args.resume:
                # If only clearing, exit
                return 0
        
        # Show resume status if --resume
        if args.resume:
            print("\n" + "="*60)
            print("Resume Mode")
            print("="*60)
            progress.show_status()
            
            if not progress.get_last_step():
                print("\n  Starting fresh (no previous progress)")
        
        # Initialize deployer
        print("\n" + "="*60)
        print("NVIDIA Air BCM Automated Deployment")
        print("="*60)
        print(f"Using API: {api_base_url}")
        if args.non_interactive:
            print("Mode: Non-interactive (using defaults)")
        if args.resume:
            print("Mode: Resume from checkpoint")
        
        try:
            deployer = AirBCMDeployer(
                api_base_url=api_base_url,
                api_token=args.api_token,
                username=None,  # Will load from env
                non_interactive=args.non_interactive,
                progress_tracker=progress
            )
        except Exception as e:
            # Authentication errors are already printed with troubleshooting info
            # Exit gracefully without traceback
            if "Login failed" in str(e) or "Authentication" in str(e):
                sys.exit(1)
            raise  # Re-raise other exceptions
        
        # Track variables that might be restored from progress
        bcm_version = None
        collection_name = None
        bcm_iso_path = None
        simulation_name = None
        cloudinit_success = False
        ssh_config_file = None
        
        # Step: BCM version selection
        if args.resume and progress.is_step_completed('bcm_version_selected'):
            bcm_version = progress.get('bcm_version')
            collection_name = progress.get('collection_name')
            iso_path_str = progress.get('bcm_iso_path')
            if iso_path_str:
                bcm_iso_path = Path(iso_path_str)
            print(f"\n  [resume] BCM version: {bcm_version}")
        elif args.bcm_version:
            # Use command-line specified version
            bcm_version, collection_name, bcm_iso_path = deployer.prompt_bcm_version(args.bcm_version)
            if not bcm_version:
                print("\n✗ Failed to resolve BCM version. Exiting.")
                sys.exit(1)
            progress.complete_step('bcm_version_selected', 
                                   bcm_version=bcm_version, 
                                   collection_name=collection_name,
                                   bcm_iso_path=str(bcm_iso_path) if bcm_iso_path else None)
        else:
            bcm_version, collection_name, bcm_iso_path = deployer.prompt_bcm_version()
            if not bcm_version:
                print("\n✗ No BCM version selected. Exiting.")
                sys.exit(1)
            progress.complete_step('bcm_version_selected', 
                                   bcm_version=bcm_version, 
                                   collection_name=collection_name,
                                   bcm_iso_path=str(bcm_iso_path) if bcm_iso_path else None)
        
        # Step: Password configuration
        if args.resume and progress.is_step_completed('password_configured'):
            deployer.default_password = progress.get('default_password', 'Nvidia1234!')
            print(f"  [resume] Using saved password")
        else:
            deployer.prompt_default_password()
            progress.complete_step('password_configured', 
                                   default_password=deployer.default_password)
        
        # Step: Simulation name
        if args.resume and progress.is_step_completed('simulation_name_set'):
            simulation_name = progress.get('simulation_name')
            print(f"  [resume] Simulation name: {simulation_name}")
        elif args.name:
            simulation_name = args.name
            print(f"\nUsing simulation name from command line: {simulation_name}")
            progress.complete_step('simulation_name_set', simulation_name=simulation_name)
        else:
            simulation_name = deployer.prompt_simulation_name()
            progress.complete_step('simulation_name_set', simulation_name=simulation_name)
        
        # Step: Create simulation
        if args.resume and progress.is_step_completed('simulation_created'):
            deployer.simulation_id = progress.get('simulation_id')
            deployer.bcm_node_name = progress.get('bcm_node_name', 'bcm-01')
            deployer.bcm_outbound_interface = progress.get('bcm_outbound_interface', 'eth0')
            deployer.bcm_management_interface = progress.get('bcm_management_interface', 'eth0')
            print(f"  [resume] Simulation ID: {deployer.simulation_id}")
            print(f"  [resume] BCM outbound interface: {deployer.bcm_outbound_interface}")
            print(f"  [resume] BCM management interface: {deployer.bcm_management_interface}")
        else:
            topology_file = Path(args.topology_file)
            if not topology_file.exists():
                print(f"\n✗ Error: Topology file not found: {topology_file}")
                sys.exit(1)
            
            deployer.create_simulation(topology_file, simulation_name)
            progress.complete_step('simulation_created', 
                                   simulation_id=deployer.simulation_id,
                                   simulation_name=simulation_name,
                                   bcm_node_name=deployer.bcm_node_name,
                                   bcm_outbound_interface=deployer.bcm_outbound_interface,
                                   bcm_management_interface=deployer.bcm_management_interface)
        
        # Step: Configure cloud-init
        if args.resume and progress.is_step_completed('cloudinit_configured'):
            cloudinit_success = progress.get('cloudinit_success', False)
            print(f"  [resume] Cloud-init configured: {cloudinit_success}")
        else:
            cloudinit_success = deployer.configure_node_passwords_cloudinit()
            progress.complete_step('cloudinit_configured', cloudinit_success=cloudinit_success)
        
        # Step: Start simulation
        if args.resume and progress.is_step_completed('simulation_started'):
            print(f"  [resume] Simulation already started")
        else:
            deployer.start_simulation()
            progress.complete_step('simulation_started')
        
        # Step: Wait for simulation loaded
        if args.resume and progress.is_step_completed('simulation_loaded'):
            print(f"  [resume] Simulation already loaded")
        else:
            if not deployer.wait_for_simulation_loaded(timeout=300):
                print("\n✗ Error: Simulation did not load in time")
                return 1
            progress.complete_step('simulation_loaded')
        
        # Step: Enable SSH service
        if args.resume and progress.is_step_completed('ssh_enabled'):
            print(f"  [resume] SSH service already enabled")
        else:
            deployer.enable_ssh_service()
            progress.complete_step('ssh_enabled')
        
        # Step: Wait for node ready
        if args.resume and progress.is_step_completed('node_ready'):
            print(f"  [resume] Node already ready")
        else:
            bcm_node = deployer.wait_for_node_ready(deployer.bcm_node_name, timeout=900)
            progress.complete_step('node_ready')
        
        # Step: Configure SSH access
        print("\n" + "="*60)
        print("Configuring SSH Access")
        print("="*60)
        
        ssh_info = deployer.get_ssh_service_info()
        
        if not ssh_info:
            print("\n✗ Error: SSH service not available")
            print("   Please enable SSH in Air UI: Services tab > Enable SSH")
            print("   Then run with --resume to continue")
            return 1
        
        if args.resume and progress.is_step_completed('ssh_configured'):
            ssh_config_file = progress.get('ssh_config_file')
            print(f"  [resume] SSH config: {ssh_config_file}")
        else:
            ssh_config_file = deployer.create_ssh_config(ssh_info, simulation_name)
            
            if not ssh_config_file:
                print("\n✗ Error: Could not create SSH config")
                return 1
            progress.complete_step('ssh_configured', ssh_config_file=str(ssh_config_file))
        
        # If cloud-init didn't work, fallback to SSH-based password configuration
        if not cloudinit_success:
            print("\n⚠ Cloud-init configuration failed, using SSH fallback...")
            deployer.configure_node_passwords(ssh_info)
        else:
            print("\n✓ Passwords configured via cloud-init (set at boot time)")
        
        # Step: Install BCM
        if not args.skip_ansible:
            if args.resume and progress.is_step_completed('bcm_installed'):
                print(f"  [resume] BCM already installed")
            else:
                deployer.install_bcm(bcm_version, ssh_config_file, bcm_iso_path)
                progress.complete_step('bcm_installed')
        else:
            print("\n--skip-ansible specified, skipping BCM installation")
        
        # Mark completed
        progress.complete_step('completed')
        
        # Print summary
        deployer.print_summary(bcm_version, ssh_config_file)
        
        # Prompt to clear progress file for next deployment
        print("\n" + "="*60)
        print("Cleanup")
        print("="*60)
        print("\nDeployment completed successfully!")
        print("Would you like to clear the progress file so the next")
        print("deployment starts fresh? (Recommended)")
        
        if args.non_interactive:
            print("  [non-interactive] Clearing progress file")
            progress.clear()
            print("  ✓ Progress file cleared")
        else:
            print("\nClear progress file? [Y/n]: ", end="")
            response = input().strip().lower()
            if response in ['', 'y', 'yes']:
                progress.clear()
                print("  ✓ Progress file cleared")
            else:
                print("  ℹ Progress file kept (use --resume to continue from checkpoint)")
        
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

