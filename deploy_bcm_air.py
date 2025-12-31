#!/usr/bin/env python3
"""
NVIDIA Air BCM Deployment Automation

This script automates the deployment of Base Command Manager (BCM) on NVIDIA Air
using stock Ubuntu 24.04 images and Ansible Galaxy playbooks.
"""

import os
import sys
import time
import json
import argparse
import subprocess
import re
import ipaddress
from datetime import datetime
from pathlib import Path
import requests
from dotenv import load_dotenv

try:
    import yaml
except ImportError:
    yaml = None  # Optional: features.yaml support requires PyYAML

# Load environment variables from .env file
load_dotenv()

def _local_namespace() -> str | None:
    """
    Optional namespace for local, on-disk artifacts (logs, progress, default ssh configs).
    Intended for separating artifacts across different .env files (e.g. external vs internal).
    """
    ns = (os.getenv("LOCAL_NAMESPACE") or "").strip()
    return ns or None


def _local_log_dir() -> Path:
    base = Path(__file__).parent / ".logs"
    ns = _local_namespace()
    return (base / ns) if ns else base


def _local_ssh_dir() -> Path:
    base = Path(__file__).parent / ".ssh"
    ns = _local_namespace()
    return (base / ns) if ns else base


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
        'features_configured',
        'completed'
    ]
    
    def __init__(self, log_dir=None):
        self.log_dir = Path(log_dir) if log_dir else _local_log_dir()
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

    def set(self, **kwargs):
        """Store arbitrary metadata without advancing the last_step."""
        for key, value in kwargs.items():
            self.data[key] = value
        self.data['last_updated'] = datetime.now().isoformat()
        self._save()
    
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
                 non_interactive=False, progress_tracker=None,
                 skip_cloud_init: bool = False,
                 skip_ssh_service: bool = False,
                 no_sdk: bool = False):
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
        # Isolation/debug toggles (defaults keep current behavior)
        self.skip_cloud_init = skip_cloud_init
        self.skip_ssh_service = skip_ssh_service
        self.no_sdk = no_sdk
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

        # Internal cluster network ("internalnet") configuration
        # NOTE: downstream installer still consumes these as management_* fields.
        self.bcm_internalnet_if_override = (os.getenv("BCM_INTERNALNET_IF") or "").strip() or None
        self.bcm_internalnet_nw_cidr = (os.getenv("BCM_INTERNALNET_NW") or "192.168.200.0/24").strip()
        
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

        # Derived internalnet parameters (populated after we know node/topology)
        self.bcm_internalnet_interface = None
        self.bcm_internalnet_ip_primary = None
        self.bcm_internalnet_ip_secondary = None
        self.bcm_internalnet_base = None
        self.bcm_internalnet_prefixlen = None

    def _derive_internalnet_params(self):
        """
        Derive internalnet base/prefixlen and the primary/secondary IPs from BCM_INTERNALNET_NW.
        Option C:
          - /24 -> primary .254, secondary .253
          - else -> primary last usable, secondary second-to-last usable

        Minimum supported subnet: /29 (must have at least 4 usable IPs requirement).
        """
        try:
            net = ipaddress.ip_network(self.bcm_internalnet_nw_cidr, strict=False)
        except Exception as e:
            raise ValueError(f"Invalid BCM_INTERNALNET_NW '{self.bcm_internalnet_nw_cidr}': {e}")

        if net.version != 4:
            raise ValueError(f"BCM_INTERNALNET_NW must be IPv4, got: {net}")

        # /29 is the smallest accepted
        if net.prefixlen > 29:
            raise ValueError(
                f"BCM_INTERNALNET_NW must be /29 or larger network (prefixlen <= 29). Got: {net}"
            )

        self.bcm_internalnet_base = str(net.network_address)
        self.bcm_internalnet_prefixlen = int(net.prefixlen)

        if net.prefixlen == 24:
            primary = ipaddress.ip_address(int(net.network_address) + 254)
            secondary = ipaddress.ip_address(int(net.network_address) + 253)
        else:
            # For IPv4 prefixlen <= 29, broadcast exists and there are at least 6 usable hosts.
            primary = ipaddress.ip_address(int(net.broadcast_address) - 1)
            secondary = ipaddress.ip_address(int(net.broadcast_address) - 2)

        self.bcm_internalnet_ip_primary = str(primary)
        self.bcm_internalnet_ip_secondary = str(secondary)

    def _collect_bcm_interfaces_from_topology_links(self, topology_data) -> list[str]:
        """
        Best-effort extraction of BCM interface names from topology links.
        """
        links = topology_data.get('content', {}).get('links', [])
        out: set[str] = set()
        for link in links:
            if len(link) != 2:
                continue
            a, b = link
            for ep in (a, b):
                if isinstance(ep, dict) and ep.get("node") == self.bcm_node_name:
                    iface = ep.get("interface")
                    if isinstance(iface, str) and iface:
                        out.add(iface)
        return sorted(out)

    def _select_internalnet_interface_from_interfaces(self, ifaces: list[str]) -> str | None:
        """
        Select internalnet interface from an available interface list (no topology).
        Priority (after env override handled elsewhere):
          - eth1 if present
          - lowest ethN where N>0
          - any iface != eth0
        """
        if not ifaces:
            return None
        if "eth1" in ifaces:
            return "eth1"
        ethN: list[tuple[int, str]] = []
        for i in ifaces:
            m = re.match(r"^eth(\d+)$", i)
            if m:
                n = int(m.group(1))
                if n > 0:
                    ethN.append((n, i))
        if ethN:
            return sorted(ethN)[0][1]
        non_eth0 = [i for i in ifaces if i != "eth0"]
        return non_eth0[0] if non_eth0 else None

    def _list_node_interfaces_from_api(self, sim_id: str, node_name: str) -> list[str]:
        """
        Best-effort interface-name discovery from Air API for an existing simulation.
        We avoid relying on a topology file in --sim-id mode.
        """
        base = self.api_base_url.rstrip("/")

        # Prefer the aggregate endpoint we already use in diagnostics.
        try:
            resp = requests.get(
                f"{base}/api/v2/simulations/nodes/interfaces/services/",
                headers=self.headers,
                params={"simulation": sim_id},
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", data) if isinstance(data, dict) else data
                ifaces: set[str] = set()
                if isinstance(results, list):
                    for row in results:
                        if not isinstance(row, dict):
                            continue
                        # Try a few likely fields.
                        rn = row.get("node_name") or row.get("node") or row.get("nodeTitle") or row.get("nodeName")
                        if rn != node_name:
                            continue
                        for k in ("interface_name", "interface", "node_interface", "iface", "name"):
                            v = row.get(k)
                            if isinstance(v, str) and v.startswith("eth"):
                                ifaces.add(v)
                            # Sometimes interface is encoded like "bcm-01:eth1"
                            if isinstance(v, str) and ":" in v:
                                tail = v.split(":")[-1]
                                if tail.startswith("eth"):
                                    ifaces.add(tail)
                return sorted(ifaces)
        except Exception:
            pass

        # Fallback: no interfaces discovered
        return []
    
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
        Detect which interface on the BCM node should be used for the internal cluster network
        ("internalnet"). This is NOT production BMC/mgmtnet; it is the installer-facing internal
        network used by BCM.
        
        Selection priority:
          a) BCM_INTERNALNET_IF (if set) - accept blindly (no validation here)
          b) if BCM has a link to oob-mgmt-switch, use that interface (legacy/back-compat)
          c) else default to eth1, then lowest ethN>0 observed in topology links, else any iface != eth0
        
        Args:
            topology_data: Parsed JSON topology data
            
        Returns:
            Interface name (e.g., 'eth0', 'eth1') or None if not found
        """
        if self.bcm_internalnet_if_override:
            print(f"  ℹ Using BCM_INTERNALNET_IF override: {self.bcm_node_name}:{self.bcm_internalnet_if_override}")
            return self.bcm_internalnet_if_override

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
                    print(f"  ✓ BCM internalnet interface detected: {self.bcm_node_name}:{iface} → oob-mgmt-switch (legacy)")
                    return iface
                if endpoint2.get('node') == self.bcm_node_name and endpoint1.get('node') == 'oob-mgmt-switch':
                    iface = endpoint2.get('interface')
                    print(f"  ✓ BCM internalnet interface detected: {self.bcm_node_name}:{iface} → oob-mgmt-switch (legacy)")
                    return iface

        ifaces = self._collect_bcm_interfaces_from_topology_links(topology_data)
        chosen = self._select_internalnet_interface_from_interfaces(ifaces)
        if chosen:
            print(f"  ℹ No oob-mgmt-switch link found; using internalnet interface default: {self.bcm_node_name}:{chosen}")
            return chosen

        print(f"  ⚠ Could not determine internalnet interface for {self.bcm_node_name} (no interfaces found in topology links)")
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
        
        # Detect which interface to use for the internal cluster network ("internalnet")
        # NOTE: downstream installer variable is still called management_interface.
        self._derive_internalnet_params()
        self.bcm_management_interface = self.detect_bcm_management_interface(topology_data)
        self.bcm_internalnet_interface = self.bcm_management_interface
        if not self.bcm_management_interface:
            print(f"  ℹ Could not detect internalnet interface; defaulting to eth1")
            self.bcm_management_interface = 'eth1'
            self.bcm_internalnet_interface = 'eth1'

        print(f"  Internalnet network: {self.bcm_internalnet_base}/{self.bcm_internalnet_prefixlen}")
        print(f"  Internalnet IP (primary): {self.bcm_internalnet_ip_primary}")
        
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
            if getattr(self, "no_sdk", False):
                raise RuntimeError("SDK disabled by --no-sdk")
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
                        # NOTE: On https://air.nvidia.com, the OpenAPI spec defines node listing as:
                        #   GET /api/v2/simulations/nodes/?simulation=<simulation_uuid>
                        # not /api/v2/simulations/<id>/nodes/
                        f"{self.api_base_url}/api/v2/simulations/nodes/",
                        headers=self.headers,
                        params={"simulation": self.simulation_id},
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

    def get_simulation_state(self) -> str | None:
        """
        Best-effort fetch of current simulation state.
        Returns e.g. NEW/STORED/LOADED/RUNNING/ERROR or None on failure.
        """
        if not getattr(self, "simulation_id", None):
            return None
        try:
            r = requests.get(
                f"{self.api_base_url}/api/v2/simulations/{self.simulation_id}/",
                headers=self.headers,
                timeout=30,
            )
            if r.status_code == 200:
                return (r.json() or {}).get("state")
        except Exception:
            return None
        return None
    
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
        last_sim_data = None
        
        while time.time() - start_time < timeout:
            try:
                response = requests.get(
                    f"{self.api_base_url}/api/v2/simulations/{self.simulation_id}/",
                    headers=self.headers,
                    timeout=30
                )
                
                if response.status_code == 200:
                    sim_data = response.json()
                    last_sim_data = sim_data
                    state = sim_data.get('state', 'unknown')
                    print(f"  Simulation state: {state}                    ", end='\r')
                    
                    if state == 'LOADED':
                        print(f"\n✓ Simulation is fully loaded!                    ")
                        return True
                    elif state in ['ERROR', 'FAILED']:
                        print(f"\n✗ Simulation failed to load: {state}")
                        self._dump_simulation_failure_diagnostics(
                            reason=f"simulation state={state} during load wait",
                            sim_data=sim_data,
                        )
                        return False
                
                time.sleep(5)
            except Exception as e:
                print(f"  Error checking simulation state: {e}                    ", end='\r')
                time.sleep(5)
        
        print(f"\n⚠ Timeout waiting for simulation to load")
        self._dump_simulation_failure_diagnostics(
            reason=f"timeout waiting for LOADED (timeout={timeout}s)",
            sim_data=last_sim_data,
        )
        return False

    def _dump_simulation_failure_diagnostics(self, reason: str, sim_data: dict | None = None) -> None:
        """
        Best-effort diagnostics dump when a simulation fails to load.
        Writes a single JSON file into .logs/ with details that often contain the root cause.
        """
        try:
            sim_id = self.simulation_id
            if not sim_id:
                return

            # Prefer progress-tracker log_dir, but ensure it's namespaced if LOCAL_NAMESPACE is set.
            log_dir = getattr(self.progress, "log_dir", None) or _local_log_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            out_path = log_dir / f"air-sim-failure-{sim_id}-{ts}.json"

            def _get_json(url: str, params: dict | None = None) -> dict:
                resp = requests.get(url, headers=self.headers, params=params, timeout=30)
                ct = (resp.headers.get("content-type") or "").lower()
                if resp.status_code == 200 and "application/json" in ct:
                    return {"ok": True, "status_code": resp.status_code, "json": resp.json(), "headers": dict(resp.headers)}
                return {
                    "ok": False,
                    "status_code": resp.status_code,
                    "headers": dict(resp.headers),
                    "text": (resp.text or "")[:4000],
                }

            base = self.api_base_url.rstrip("/")
            diag: dict = {
                "timestamp": datetime.now().isoformat(),
                "reason": reason,
                "api_base_url": base,
                "simulation_id": sim_id,
                "simulation_name": getattr(self, "simulation_name", None),
                "sim_data_from_wait_loop": sim_data,
                "endpoints": {},
                "sdk": {"available": False},
            }

            # SDK (when installed) can expose additional objects/fields and clearer errors than raw REST.
            # https://docs.nvidia.com/networking-ethernet-software/nvidia-air/Air-Python-SDK/
            try:
                if getattr(self, "no_sdk", False):
                    raise RuntimeError("SDK disabled by --no-sdk")
                from air_sdk import AirApi  # type: ignore

                air = AirApi(username=self.username, password=self.api_token, api_url=base)
                diag["sdk"]["available"] = True

                def _sdk_safe(obj, max_len: int = 20000):
                    try:
                        if obj is None:
                            return None
                        if hasattr(obj, "json") and callable(getattr(obj, "json")):
                            return obj.json()
                        # Fall back to repr (truncated)
                        s = repr(obj)
                        return s if len(s) <= max_len else s[:max_len] + "..."
                    except Exception as e:
                        return {"error": str(e)}

                # Simulation (often includes fields not present in our v2 REST response)
                try:
                    sim_obj = air.simulations.get(sim_id)
                    diag["sdk"]["simulation"] = _sdk_safe(sim_obj)
                except Exception as e:
                    diag["sdk"]["simulation_error"] = str(e)

                # Jobs (useful for “capacity/scheduling” failures; filter by simulation when supported)
                try:
                    jobs_api = getattr(air, "jobs", None)
                    if jobs_api and hasattr(jobs_api, "list"):
                        diag["sdk"]["jobs"] = _sdk_safe(jobs_api.list(simulation=sim_id))
                except Exception as e:
                    diag["sdk"]["jobs_error"] = str(e)

                # Simulation nodes (often includes per-node state/error)
                try:
                    sim_nodes_api = getattr(air, "simulation_nodes", None)
                    if sim_nodes_api and hasattr(sim_nodes_api, "list"):
                        diag["sdk"]["simulation_nodes"] = _sdk_safe(sim_nodes_api.list(simulation=sim_id))
                except Exception as e:
                    diag["sdk"]["simulation_nodes_error"] = str(e)

                # Capacity (if available, can immediately explain “can’t place this sim right now”)
                try:
                    capacity_api = getattr(air, "capacity", None)
                    if capacity_api and hasattr(capacity_api, "get"):
                        diag["sdk"]["capacity"] = _sdk_safe(capacity_api.get())
                except Exception as e:
                    diag["sdk"]["capacity_error"] = str(e)
            except Exception as e:
                diag["sdk"]["available"] = False
                diag["sdk"]["import_error"] = str(e)

            # v2 sim details (often contains state_message/error_message)
            diag["endpoints"]["simulation"] = _get_json(f"{base}/api/v2/simulations/{sim_id}/")
            # nodes (node-level error_message is very helpful)
            # NOTE: external air.nvidia.com does not expose /api/v2/simulations/{id}/nodes/
            # The OpenAPI spec defines /api/v2/simulations/nodes/?simulation=<id>
            diag["endpoints"]["nodes"] = _get_json(
                f"{base}/api/v2/simulations/nodes/",
                params={"simulation": sim_id},
            )
            # jobs + events (usually show the failing operation/reason)
            # NOTE: OpenAPI spec defines /api/v2/jobs/?simulation=<id>
            diag["endpoints"]["jobs"] = _get_json(
                f"{base}/api/v2/jobs/",
                params={"simulation": sim_id},
            )
            # v1 job endpoints often include additional fields (e.g., "notes") that can contain failure reasons.
            # /api/v2/jobs/{id}/ returns JobShort for non-worker clients (no error details).
            try:
                jobs_json = diag["endpoints"]["jobs"].get("json") if isinstance(diag["endpoints"]["jobs"], dict) else None
                v2_job_results = []
                if isinstance(jobs_json, dict) and isinstance(jobs_json.get("results"), list):
                    v2_job_results = jobs_json["results"]
                failed_job_ids = [
                    j.get("id")
                    for j in v2_job_results
                    if isinstance(j, dict) and j.get("state") == "FAILED"
                ]
                # Prioritize START failures
                failed_job_ids = (
                    [j.get("id") for j in v2_job_results if isinstance(j, dict) and j.get("state") == "FAILED" and j.get("category") == "START"]
                    + [jid for jid in failed_job_ids if jid]
                )
                # De-dupe while preserving order
                seen = set()
                failed_job_ids = [jid for jid in failed_job_ids if jid and not (jid in seen or seen.add(jid))]

                diag["endpoints"]["jobs_v1_failed_details"] = {}
                for jid in failed_job_ids[:5]:
                    diag["endpoints"]["jobs_v1_failed_details"][jid] = _get_json(f"{base}/api/v1/job/{jid}/")
                # If v1 job includes a worker URL, fetch that too (often has availability/health clues).
                worker_urls = []
                for job_detail in diag["endpoints"]["jobs_v1_failed_details"].values():
                    if not isinstance(job_detail, dict):
                        continue
                    j = job_detail.get("json")
                    if isinstance(j, dict) and isinstance(j.get("worker"), str) and j["worker"]:
                        worker_urls.append(j["worker"])
                # De-dupe while preserving order
                seen_w = set()
                worker_urls = [u for u in worker_urls if not (u in seen_w or seen_w.add(u))]
                diag["endpoints"]["workers_v1"] = {}
                for wurl in worker_urls[:3]:
                    diag["endpoints"]["workers_v1"][wurl] = _get_json(wurl)
            except Exception as e:
                diag["endpoints"]["jobs_v1_failed_details_error"] = str(e)

            # v1 simulation often has additional fields compared to v2 (sometimes including worker assignment).
            diag["endpoints"]["simulation_v1"] = _get_json(f"{base}/api/v1/simulation/{sim_id}/")
            # NOTE: "events" endpoint is not present in the provided OpenAPI spec for this API host.
            # Keep a placeholder so the diagnostics format is stable; callers can inspect other sources.
            diag["endpoints"]["events"] = {"ok": False, "status_code": None, "text": "No /api/v2/*events* endpoint found in .docs/NVIDIA Air API.yaml"}
            # services (v2 list is under simulations/nodes/interfaces/services/?simulation=<id>
            diag["endpoints"]["services_v2"] = _get_json(
                f"{base}/api/v2/simulations/nodes/interfaces/services/",
                params={"simulation": sim_id},
            )
            # services (v1 has useful src_port info; filter to this simulation)
            diag["endpoints"]["services_v1"] = _get_json(f"{base}/api/v1/service/", params={"simulation": sim_id})

            out_path.write_text(json.dumps(diag, indent=2, default=str))
            print(f"\n  ℹ Wrote Air failure diagnostics: {out_path}")
            print(f"  ℹ Tip: you can also run: python scripts/air-tests/get_sim_info.py --sim-id {sim_id}")
        except Exception as e:
            # Never fail the deploy because diagnostics failed
            print(f"\n  Warning: failed to write Air diagnostics: {e}")

    def delete_simulation(self) -> bool:
        """Best-effort delete of the current simulation."""
        sim_id = getattr(self, "simulation_id", None)
        if not sim_id:
            return False
        try:
            resp = requests.delete(
                f"{self.api_base_url}/api/v2/simulations/{sim_id}/",
                headers=self.headers,
                timeout=60,
            )
            return resp.status_code in (200, 202, 204)
        except Exception:
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
        print(f"  Ensuring SSH service on {self.bcm_node_name}:{interface}...")

        if getattr(self, "skip_ssh_service", False):
            print("  ℹ Skipping SSH service creation (--skip-ssh-service)")
            return None

        # If an SSH service already exists for this node/interface, reuse it.
        existing = self.get_ssh_service_info(node_name=self.bcm_node_name, interface=interface)
        if existing and existing.get("hostname") and existing.get("port"):
            print(f"  ✓ SSH service already exists (reusing): {existing.get('hostname')}:{existing.get('port')}")
            return existing
        
        try:
            if getattr(self, "no_sdk", False):
                raise RuntimeError("SDK disabled by --no-sdk")
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
    
    def get_ssh_service_info(self, node_name: str | None = None, interface: str | None = None):
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
                
                target_node = node_name or self.bcm_node_name
                target_iface = interface

                def _matches_iface(svc: dict) -> bool:
                    if not target_iface:
                        return True
                    # Common field names seen in Air v1 service objects.
                    for k in ("interface", "node_interface", "interface_name"):
                        v = svc.get(k)
                        if isinstance(v, str):
                            # could be "bcm-01:eth0" or "eth0"
                            if v == target_iface or v.endswith(f":{target_iface}"):
                                return True
                    return False

                # Prefer matching node+iface if possible; otherwise fall back to first SSH service for node.
                best = None
                fallback = None
                for service in services:
                    if service.get('service_type') != 'ssh':
                        continue
                    if service.get('node_name') != target_node:
                        continue
                    if fallback is None:
                        fallback = service
                    if _matches_iface(service):
                        best = service
                        break

                chosen = best or fallback
                if chosen:
                    return {
                        'hostname': chosen.get('host'),
                        'port': chosen.get('src_port'),  # src_port is the external port
                        'username': 'root',  # BCM uses root, configured via cloud-init
                        'link': chosen.get('link'),
                        'service_id': chosen.get('id')
                    }
            
            return None
            
        except Exception as e:
            print(f"  Warning: Could not retrieve SSH service info: {e}")
            return None
    
    def ensure_userconfig(self):
        """
        Ensure the UserConfig for cloud-init exists (create or find).
        
        UserConfigs are user-level (not simulation-specific), so this can be
        called BEFORE simulation creation to avoid rate limiting issues.
        
        Returns:
            userconfig_id if successful, None otherwise
        """
        print("\nEnsuring cloud-init UserConfig exists...")
        print(f"  Target password: {self.default_password}")
        
        # Ensure cloud-init config exists (auto-generate from template if needed)
        try:
            cloudinit_template_path = self.ensure_cloud_init_config()
        except FileNotFoundError as e:
            print(f"  ⚠ {e}")
            return None
        
        # Read template and substitute password
        cloudinit_template = cloudinit_template_path.read_text()
        cloudinit_content = cloudinit_template.replace('{PASSWORD}', self.default_password)
        
        userdata_name = "bcm-cloudinit-password"  # Fixed name for reuse
        
        headers = {
            "Authorization": f"Bearer {self.jwt_token}",
            "Content-Type": "application/json"
        }
        
        userdata_id = None
        
        # First, check if we already have this config (avoids 403 on create)
        try:
            print("  Checking for existing UserConfig...")
            list_response = requests.get(
                f"{self.api_base_url}/api/v2/userconfigs/",
                headers=headers,
                timeout=30
            )
            if list_response.status_code == 200:
                for cfg in list_response.json().get('results', []):
                    if cfg.get('name') == userdata_name:
                        userdata_id = cfg.get('id')
                        print(f"    ✓ Found existing UserConfig: {userdata_id}")
                        
                        # Update the content in case password changed
                        update_response = requests.patch(
                            f"{self.api_base_url}/api/v2/userconfigs/{userdata_id}/",
                            headers=headers,
                            json={"content": cloudinit_content},
                            timeout=30
                        )
                        if update_response.status_code == 200:
                            print(f"    ✓ Updated UserConfig content")
                        break
            elif list_response.status_code == 403:
                print(f"    ⚠ Cannot list UserConfigs (403 - may be free tier limitation)")
        except Exception as e:
            if os.getenv('DEBUG'):
                print(f"    [DEBUG] List failed: {e}")
        
        # If not found, try to create it
        if not userdata_id:
            print("  Creating new UserConfig...")
            payload = {
                "name": userdata_name,
                "kind": "cloud-init-user-data",
                "organization": None,  # Must be explicitly sent, not omitted
                "content": cloudinit_content
            }
            
            if os.getenv('DEBUG'):
                print(f"    [DEBUG] POST {self.api_base_url}/api/v2/userconfigs/")
                print(f"    [DEBUG] Content size: {len(cloudinit_content)} bytes")
            
            response = requests.post(
                f"{self.api_base_url}/api/v2/userconfigs/",
                headers=headers,
                json=payload,
                timeout=30
            )
            
            if response.status_code == 201:
                userdata_id = response.json().get('id')
                print(f"    ✓ Created UserConfig: {userdata_id}")
            else:
                error_str = response.text.lower()
                print(f"    ⚠ Error creating UserConfig: {response.status_code}")
                
                if response.status_code == 403 or 'forbidden' in error_str or 'permission' in error_str:
                    print(f"\n    ℹ This may be a free tier limitation on air.nvidia.com")
                    print(f"    ℹ Cloud-init/UserConfig may require a paid subscription")
                    print(f"    ℹ The script will continue with default passwords")
                else:
                    print(f"    Response: {response.text[:300]}")
        
        # Store for later use
        self.userconfig_id = userdata_id
        return userdata_id
    
    def configure_node_passwords_cloudinit(self):
        """
        Assign cloud-init UserConfig to simulation nodes.
        
        Requires:
        - self.userconfig_id to be set (call ensure_userconfig() first)
        - Simulation to exist (self.simulation_id)
        
        Returns:
            True if successful, False otherwise
        """
        print("\nAssigning cloud-init to simulation nodes...")

        if getattr(self, "skip_cloud_init", False):
            print("  ℹ Skipping cloud-init assignment (--skip-cloud-init)")
            return False
        
        # Check if we have a UserConfig to assign
        userdata_id = getattr(self, 'userconfig_id', None)
        if not userdata_id:
            print("  ⚠ No UserConfig available (ensure_userconfig() not called or failed)")
            return False
        
        print(f"  Using UserConfig: {userdata_id}")
        
        try:
            if getattr(self, "no_sdk", False):
                raise RuntimeError("SDK disabled by --no-sdk")
            from air_sdk import AirApi
        except ImportError:
            print("  ⚠ air_sdk not installed. Install with: pip install air-sdk")
            return False
        except Exception as e:
            print(f"  ⚠ {e}")
            return False
        
        try:
            # Initialize Air SDK for node operations
            print("  Connecting to Air SDK...")
            air = AirApi(
                username=self.username,
                password=self.api_token,
                api_url=self.api_base_url
            )
            
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
                    # SDK expects a dictionary with 'user_data' key containing the config ID
                    node.set_cloud_init_assignment({'user_data': userdata_id})
                    print(f"    ✓ Cloud-init assigned to {node_name}")
                    configured_count += 1
                except Exception as e:
                    print(f"    ⚠ Could not assign cloud-init to {node_name}: {e}")
            
            if skipped_nodes:
                print(f"\n  ℹ Skipped nodes (don't support cloud-init):")
                for name, reason in skipped_nodes:
                    print(f"    - {name} ({reason})")
            
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
        Configure BCM head node via SSH (fallback when cloud-init unavailable)
        
        This handles:
        1. Password change prompt (if present on first login)
        2. Setting new password
        3. Copying SSH public key for key-based auth
        
        Args:
            ssh_info: dict with SSH connection details
            
        Returns:
            (ok, details) where details contains:
              - bootstrap_tool: "sshpass" | "expect" | None
              - password_change_prompt: bool | None
              - bootstrap_method: str (stable label for logs)
        """
        if not ssh_info:
            print("  ⚠ Cannot configure node without SSH service info")
            return False, {
                "bootstrap_tool": None,
                "password_change_prompt": None,
                "bootstrap_method": "ssh-missing-ssh-info",
            }
        
        print("\nConfiguring node passwords via SSH...")
        print(f"  Target: {ssh_info['hostname']}:{ssh_info['port']}")
        print(f"  Default user: ubuntu, default password: nvidia")
        print(f"  New password: {self.default_password}")
        
        # Read SSH public key if available
        ssh_pubkey = None
        if self.ssh_public_key and Path(self.ssh_public_key).expanduser().exists():
            ssh_pubkey = Path(self.ssh_public_key).expanduser().read_text().strip()
            print(f"  SSH public key: {self.ssh_public_key}")
        
        desired_hostname = getattr(self, "bcm_node_name", "bcm-01")

        # Create a shell script to run on the remote host
        # NOTE: This is fed via stdin to "bash -s" (no SCP) to avoid brittle scp/expect flows.
        setup_script_content = f'''#!/bin/bash
set -e

echo "Configuring BCM head node..."

# Set hostname (cloud-init usually does this; SSH bootstrap needs to be explicit)
sudo hostnamectl set-hostname "{desired_hostname}"
if grep -qE "^127\\.0\\.1\\.1\\s+" /etc/hosts; then
  sudo sed -i -E "s/^127\\.0\\.1\\.1\\s+.*/127.0.1.1 {desired_hostname}/" /etc/hosts
else
  echo "127.0.1.1 {desired_hostname}" | sudo tee -a /etc/hosts >/dev/null
fi
echo "  ✓ Hostname set to {desired_hostname}"

# Change ubuntu password
echo "ubuntu:{self.default_password}" | sudo chpasswd
echo "  ✓ Ubuntu password changed"

# Change root password  
echo "root:{self.default_password}" | sudo chpasswd
echo "  ✓ Root password changed"

# Enable root SSH login
sudo sed -i 's/^#*PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
echo "  ✓ Root SSH login enabled"

if [ -n "{ssh_pubkey or ''}" ]; then
  # Add SSH key for ubuntu user
  mkdir -p ~/.ssh
  chmod 700 ~/.ssh
  echo '{ssh_pubkey or ''}' >> ~/.ssh/authorized_keys
  chmod 600 ~/.ssh/authorized_keys
  sort -u ~/.ssh/authorized_keys -o ~/.ssh/authorized_keys
  echo "  ✓ Ubuntu SSH key added"

  # Add SSH key for root user
  sudo mkdir -p /root/.ssh
  sudo chmod 700 /root/.ssh
  echo '{ssh_pubkey or ''}' | sudo tee -a /root/.ssh/authorized_keys > /dev/null
  sudo chmod 600 /root/.ssh/authorized_keys
  echo "  ✓ Root SSH key added"
else
  echo "  ⚠ No SSH public key provided; skipping key setup"
fi

# Restart SSH and wait
sudo systemctl restart ssh
sleep 3
echo "  ✓ SSH service restarted"
echo "SETUP_COMPLETE"
'''
        
        # Write setup script to a unique temp file (avoid collisions across concurrent runs)
        import tempfile
        ns = _local_namespace() or "default"
        with tempfile.NamedTemporaryFile(prefix=f"air_node_setup_{ns}_", suffix=".sh", delete=False) as tf:
            setup_script_file = Path(tf.name)
        setup_script_file.write_text(setup_script_content)
        setup_script_file.chmod(0o755)
        
        host = ssh_info['hostname']
        port = ssh_info['port']
        default_pass = "nvidia"
        
        details = {
            "bootstrap_tool": None,
            "password_change_prompt": None,
            "bootstrap_method": "ssh-unknown",
        }

        try:
            # Wait for the SSH service to actually accept connections.
            # We see occasional "Connection refused" even though the service exists in the API.
            print("\n  Waiting for SSH service to accept connections...")
            common_opts = [
                '-o', 'StrictHostKeyChecking=no',
                '-o', 'UserKnownHostsFile=/dev/null',
                '-o', 'ConnectTimeout=5',
                '-o', 'ConnectionAttempts=1',
            ]
            ready = False
            for attempt in range(1, 31):  # ~60-90s depending on sleep
                sshpass_probe = subprocess.run(['which', 'sshpass'], capture_output=True).returncode == 0
                if sshpass_probe:
                    probe_cmd = ['sshpass', '-p', default_pass, 'ssh'] + common_opts + [
                        '-p', str(port),
                        f'ubuntu@{host}',
                        'echo PROBE_OK'
                    ]
                    pr = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=15)
                    if pr.returncode == 0 and "PROBE_OK" in (pr.stdout or ""):
                        ready = True
                        break
                else:
                    # Try a raw TCP connect check (best-effort) using bash builtin.
                    pr = subprocess.run(
                        ["bash", "-lc", f"timeout 3 bash -lc '</dev/tcp/{host}/{port}'"],
                        capture_output=True,
                        text=True,
                    )
                    if pr.returncode == 0:
                        ready = True
                        break
                if attempt % 5 == 0:
                    print(f"  (still waiting... attempt {attempt}/30)")
                time.sleep(3)
            if not ready:
                print("  ⚠ SSH service did not become ready in time (continuing anyway)")

            # Check if sshpass is available (preferred method)
            sshpass_available = subprocess.run(['which', 'sshpass'], capture_output=True).returncode == 0
            try_expect_fallback = False
            
            if sshpass_available:
                print("\n  Using sshpass for password authentication...")
                details["bootstrap_tool"] = "sshpass"

                # Execute the setup script via stdin (avoids SCP and is more reliable)
                print("  Executing setup script (via ssh stdin)...")
                ssh_cmd = ['sshpass', '-p', default_pass, 'ssh'] + common_opts + [
                    '-p', str(port),
                    f'ubuntu@{host}',
                    'bash -s'
                ]
                result = subprocess.run(
                    ssh_cmd,
                    input=setup_script_content,
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                
                # Show output
                if result.stdout:
                    for line in result.stdout.strip().split('\n'):
                        if line.strip():
                            print(f"  {line}")
                
                if 'SETUP_COMPLETE' in (result.stdout or ""):
                    print("\n  ✓ Node configuration complete")
                    # If sshpass succeeded, we necessarily did NOT hit an interactive forced password-change flow.
                    details["password_change_prompt"] = False
                    details["bootstrap_method"] = "ssh-sshpass-no-pwchange"
                    return True, details
                else:
                    print(f"\n  ⚠ Setup may not have completed fully")
                    if result.stderr:
                        print(f"    stderr: {result.stderr[:200]}")
                    # If the image requires an interactive password-change flow, sshpass won't handle it.
                    # Fall back to expect which does.
                    combined = (result.stdout or "") + "\n" + (result.stderr or "")
                    if any(s in combined.lower() for s in ["current password", "new password", "password expired", "must change", "you are required to change"]):
                        print("  ℹ Detected a forced password-change prompt; falling back to expect...")
                        try_expect_fallback = True
                        details["password_change_prompt"] = True
                    else:
                        print("  ℹ No forced password-change prompt detected (or not observable via sshpass).")
                    if not try_expect_fallback:
                        details["bootstrap_method"] = "ssh-sshpass-failed"
                        return False, details

            if (not sshpass_available) or try_expect_fallback:
                # Fallback to expect
                print("\n  Using expect fallback (handles forced password-change prompts)...")
                details["bootstrap_tool"] = "expect"
                if not _command_exists("expect"):
                    print("  ⚠ Required tool not found: expect")
                    print("  ⚠ Install sshpass or expect: sudo apt install sshpass expect")
                    details["bootstrap_method"] = "ssh-expect-missing"
                    return False, details

                import base64
                setup_script_b64 = base64.b64encode(setup_script_content.encode("utf-8")).decode("ascii")

                # Create expect script.
                # This mode handles the "forced password change on first login" flow
                # and runs our setup script by decoding base64 remotely (no scp).
                expect_script = f'''#!/usr/bin/expect -f
set timeout 180
set host "{host}"
set port "{port}"
set oldpw "nvidia"
set newpw "{self.default_password}"
set saw_pwchange 0

proc handle_pwchange {{}} {{
    upvar saw_pwchange saw_pwchange
    # Common forced-change prompts differ slightly across images.
    expect {{
        -re "(?i)current.*password" {{
            set saw_pwchange 1
            puts "\\nℹ Detected forced password-change prompt (current password)"
            send "$oldpw\\r"
            exp_continue
        }}
        -re "(?i)enter new.*password|(?i)new.*password" {{
            set saw_pwchange 1
            puts "\\nℹ Detected forced password-change prompt (new password)"
            send "$newpw\\r"
            exp_continue
        }}
        -re "(?i)retype new.*password|(?i)repeat new.*password" {{
            set saw_pwchange 1
            puts "\\nℹ Detected forced password-change prompt (retype new password)"
            send "$newpw\\r"
            exp_continue
        }}
        -re "\\$ $" {{ return }}
        -re "# $" {{ return }}
        timeout {{ return }}
    }}
}}

spawn ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p $port ubuntu@$host "bash -s"
expect {{
    -re "(?i)are you sure you want to continue connecting" {{ send "yes\\r"; exp_continue }}
    -re "(?i)password:" {{ send "$oldpw\\r"; handle_pwchange }}
    timeout {{ puts "\\n✗ Timed out waiting for password prompt"; exit 2 }}
}}

if {{$saw_pwchange == 0}} {{
    puts "\\nℹ No forced password-change prompt detected (logged in directly)"
}}

# Now run the setup script by decoding base64 on the remote side.
send -- "echo {setup_script_b64} | base64 -d | bash\\r"

expect {{
    -re "SETUP_COMPLETE" {{ puts "\\n✓ BCM head node configured successfully"; exit 0 }}
    timeout {{ puts "\\n✗ Timed out waiting for SETUP_COMPLETE"; exit 3 }}
}}
'''
                with tempfile.NamedTemporaryFile(prefix=f"air_password_config_{ns}_", suffix=".exp", delete=False) as tf:
                    expect_file = Path(tf.name)
                expect_file.write_text(expect_script)
                expect_file.chmod(0o700)

                result = subprocess.run(
                    ['expect', str(expect_file)],
                    capture_output=True,
                    text=True,
                    timeout=240
                )

                if result.stdout:
                    for line in result.stdout.strip().split('\n'):
                        if line.strip() and not line.startswith('spawn'):
                            print(f"  {line}")

                if result.returncode == 0 or 'SETUP_COMPLETE' in (result.stdout or ""):
                    out = (result.stdout or "")
                    saw_pwchange = "Detected forced password-change prompt" in out
                    # Expect script explicitly prints one of:
                    #   - "Detected forced password-change prompt ..."
                    #   - "No forced password-change prompt detected (logged in directly)"
                    details["password_change_prompt"] = saw_pwchange
                    details["bootstrap_method"] = "ssh-expect-pwchange" if saw_pwchange else "ssh-expect-no-pwchange"
                    print("\n  ✓ Node configuration complete")
                    return True, details
                else:
                    print(f"\n  ⚠ Configuration may have issues")
                    if result.stderr:
                        print(f"    stderr: {result.stderr[:200]}")
                    details["bootstrap_method"] = "ssh-expect-failed"
                    return False, details
                    
        except FileNotFoundError as e:
            print(f"  ⚠ Required tool not found: {e}")
            print("  ⚠ Install sshpass or expect: sudo apt install sshpass expect")
            print("  ⚠ Manual configuration required:")
            print(f"    1. SSH: ssh -p {port} ubuntu@{host}")
            print(f"    2. Password: nvidia")
            print(f"    3. Run: bash -c 'echo \"ubuntu:{self.default_password}\" | sudo chpasswd'")
            print(f"    4. Add your SSH key to ~/.ssh/authorized_keys")
            details["bootstrap_method"] = "ssh-tool-missing"
            return False, details
        except subprocess.TimeoutExpired:
            print("  ⚠ Configuration timed out")
            details["bootstrap_method"] = "ssh-timeout"
            return False, details
        except Exception as e:
            print(f"  ⚠ Error configuring node: {e}")
            details["bootstrap_method"] = "ssh-error"
            return False, details
        finally:
            # Clean up temp files
            setup_script_file.unlink(missing_ok=True)
            try:
                expect_file.unlink(missing_ok=True)  # type: ignore[name-defined]
            except Exception:
                pass
    
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
        sim_name_slug = simulation_name.replace(' ', '-')

        # Allow overriding the SSH config output path via env var so different env files
        # (.env, .env.external, .env.internal) can write to different locations/names.
        #
        # Examples:
        #   AIR_SSH_CONFIG_FILE=~/.ssh/air-external.conf
        #   AIR_SSH_CONFIG_FILE=~/.ssh/air/{simulation_name_slug}.conf
        #   AIR_SSH_CONFIG_FILE=./.ssh/internal/{simulation_id}
        #
        # If a directory is provided, we will write <dir>/<simulation_name_slug>.
        ssh_config_override = os.getenv("AIR_SSH_CONFIG_FILE") or os.getenv("BCM_SSH_CONFIG_FILE")

        config_file: Path
        if ssh_config_override:
            raw = os.path.expanduser(ssh_config_override.strip())
            if not raw:
                print("  ⚠ AIR_SSH_CONFIG_FILE is set but empty; falling back to default ./.ssh/<simulation>")
                ssh_config_override = None
            else:
                fmt = {
                    "simulation_name": simulation_name,
                    "simulation_name_slug": sim_name_slug,
                    "simulation_id": self.simulation_id or "",
                }
                try:
                    resolved = raw.format_map(fmt) if "{" in raw else raw
                except Exception as e:
                    print(f"  ⚠ Could not format AIR_SSH_CONFIG_FILE='{ssh_config_override}': {e}")
                    print("  ⚠ Falling back to default ./.ssh/<simulation>")
                    resolved = ""
                    ssh_config_override = None

                if ssh_config_override:
                    p = Path(resolved)
                    # If the override is (or looks like) a directory, append default filename.
                    if str(resolved).endswith(os.sep) or (p.exists() and p.is_dir()):
                        p = p / sim_name_slug
                    config_file = p

        if not ssh_config_override:
            # Default: create .ssh directory in project and use simulation name for config filename
            project_ssh_dir = _local_ssh_dir()
            project_ssh_dir.mkdir(mode=0o700, exist_ok=True)
            config_file = project_ssh_dir / sim_name_slug
        
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
        config_file.parent.mkdir(parents=True, exist_ok=True)
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
        
        # Wait for SSH service to stabilize after potential restart
        print(f"  Waiting for SSH service to stabilize...")
        time.sleep(15)
        
        # Verify SSH key authentication works before attempting rsync
        print(f"  Verifying SSH key authentication...")
        ssh_test_cmd = [
            'ssh', '-F', str(ssh_config_file),
            '-o', 'BatchMode=yes',  # Fail if password required
            '-o', 'ConnectTimeout=10',
            f'air-{self.bcm_node_name}',
            'echo SSH_KEY_AUTH_OK'
        ]
        try:
            test_result = subprocess.run(ssh_test_cmd, capture_output=True, text=True, timeout=30)
            if 'SSH_KEY_AUTH_OK' in test_result.stdout:
                print(f"  ✓ SSH key authentication verified")
            else:
                print(f"  ⚠ SSH key auth may not be working, will try anyway...")
                print(f"    stdout: {test_result.stdout[:100] if test_result.stdout else 'empty'}")
                print(f"    stderr: {test_result.stderr[:100] if test_result.stderr else 'empty'}")
        except Exception as e:
            print(f"  ⚠ Could not verify SSH key auth: {e}")
            print(f"  ⚠ Continuing anyway - rsync may prompt for password")
        
        # Use rsync for reliable large file transfer
        # Upload to /home/ubuntu/ since we connect as ubuntu user
        ssh_cmd = f"ssh -F {ssh_config_file} -o StrictHostKeyChecking=no"
        remote_path = f"air-{self.bcm_node_name}:/home/ubuntu/bcm.iso"
        
        # Reduce rsync verbosity: single progress line instead of per-file progress spam
        cmd = [
            'rsync',
            '-az',
            '--partial',             # Keep partial files on interrupt (enables resume)
            '--info=progress2',      # Single progress line
            '--no-inc-recursive',    # More stable progress output for large files
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
        script_content = script_content.replace('__BCM_FULL_VERSION__', bcm_version)
        script_content = script_content.replace('__ADMIN_EMAIL__', self.bcm_admin_email)
        script_content = script_content.replace('__EXTERNAL_INTERFACE__', self.bcm_outbound_interface)
        script_content = script_content.replace('__MANAGEMENT_INTERFACE__', self.bcm_management_interface)
        script_content = script_content.replace('__INTERNALNET_IP__', str(self.bcm_internalnet_ip_primary or ""))
        script_content = script_content.replace('__INTERNALNET_BASE__', str(self.bcm_internalnet_base or ""))
        script_content = script_content.replace('__INTERNALNET_PREFIXLEN__', str(self.bcm_internalnet_prefixlen or ""))
        
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
    
    def upload_bcm_collection_patch(self, bcm_version, ssh_config_file):
        """
        Upload an optional per-BCM-version collection patch script to the head node.

        If scripts/patches/<bcm_version>.py exists locally, upload it to:
          /home/ubuntu/bcm_patches/<bcm_version>.py
        """
        patch_src = Path(__file__).parent / 'scripts' / 'patches' / f'{bcm_version}.py'
        if not patch_src.exists():
            print("  ℹ No BCM collection patch for this version")
            return True

        print(f"\n🩹 Uploading BCM collection patch: {patch_src.name}...")

        remote_dir = "/home/ubuntu/bcm_patches"
        ssh_mkdir = [
            'ssh',
            '-F', ssh_config_file,
            '-o', 'StrictHostKeyChecking=no',
            f'air-{self.bcm_node_name}',
            f'mkdir -p {remote_dir}'
        ]
        scp_cmd = [
            'scp',
            '-F', ssh_config_file,
            '-o', 'StrictHostKeyChecking=no',
            str(patch_src),
            f"air-{self.bcm_node_name}:{remote_dir}/{patch_src.name}",
        ]
        try:
            subprocess.run(ssh_mkdir, check=True, capture_output=True)
            subprocess.run(scp_cmd, check=True, capture_output=True, text=True)
            print("  ✓ Patch uploaded")
            return True
        except subprocess.CalledProcessError as e:
            print(f"\n✗ Patch upload failed: {e}")
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
        
        # Step 3: Upload install script (bcm-ansible-installer is cloned on remote host)
        if not self.upload_install_script(bcm_version, ssh_config_file):
            raise RuntimeError("Failed to upload installation script")

        # Step 4: Upload optional per-version patch for the installed Ansible collection
        if not self.upload_bcm_collection_patch(bcm_version, ssh_config_file):
            raise RuntimeError("Failed to upload BCM collection patch")
        
        # Step 5: Execute installation
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
        if getattr(self, "bcm_internalnet_ip_primary", None) and getattr(self, "bcm_internalnet_base", None) and getattr(self, "bcm_internalnet_prefixlen", None):
            print(f"  Internalnet: {self.bcm_internalnet_base}/{self.bcm_internalnet_prefixlen} -> {self.bcm_internalnet_ip_primary}")
        else:
            print(f"  Internalnet: 192.168.200.0/24 -> 192.168.200.254")
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
    
    def load_topology_features(self, topology_dir):
        """
        Load features.yaml from a topology directory
        
        Args:
            topology_dir: Path to topology directory containing features.yaml
            
        Returns:
            dict: Features configuration, or empty dict if not found
        """
        if yaml is None:
            print("  ⚠ PyYAML not installed - features.yaml support disabled")
            return {}
        
        features_path = Path(topology_dir) / 'features.yaml'
        if not features_path.exists():
            print(f"  ℹ No features.yaml found in {topology_dir}")
            return {}
        
        try:
            with open(features_path, 'r') as f:
                features = yaml.safe_load(f) or {}
            return features
        except Exception as e:
            print(f"  ⚠ Error loading features.yaml: {e}")
            return {}
    
    def _resolve_versioned_value(self, value, bcm_major: str):
        """
        Resolve a value that can be either:
          - a string (returned as-is)
          - a dict keyed by BCM major version ("10"/"11" or 10/11)
        """
        if isinstance(value, dict):
            # try string key first, then int key
            if bcm_major in value:
                return value[bcm_major]
            try:
                k = int(bcm_major)
                if k in value:
                    return value[k]
            except Exception:
                pass
        return value

    def _managed_reboot(self, ssh_config_file: str, timeout: int = 900) -> bool:
        """
        Reboot BCM node and wait for SSH to come back.
        This is intended for post-install steps where a reboot is required for settings to apply.
        """
        print("\n  🔁 Managed reboot requested...")
        host = f"air-{self.bcm_node_name}"

        # Trigger reboot (connection likely drops; treat non-zero as expected)
        try:
            subprocess.run(
                [
                    "ssh",
                    "-F",
                    ssh_config_file,
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "UserKnownHostsFile=/dev/null",
                    host,
                    "sudo reboot || true",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception:
            pass

        # Wait for SSH to return
        print("  ⏳ Waiting for SSH to go down and come back...")
        start = time.time()
        last_msg = 0.0
        while time.time() - start < timeout:
            try:
                r = subprocess.run(
                    [
                        "ssh",
                        "-F",
                        ssh_config_file,
                        "-o",
                        "BatchMode=yes",
                        "-o",
                        "ConnectTimeout=5",
                        "-o",
                        "StrictHostKeyChecking=no",
                        "-o",
                        "UserKnownHostsFile=/dev/null",
                        host,
                        "echo REBOOT_OK",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if r.returncode == 0 and "REBOOT_OK" in (r.stdout or ""):
                    print("  ✓ SSH is back after reboot")
                    # Give services a moment to settle
                    time.sleep(10)
                    return True
            except Exception:
                pass

            if time.time() - last_msg > 30:
                print("  (still waiting for SSH...)")
                last_msg = time.time()
            time.sleep(5)

        print(f"  ✗ Timed out waiting for SSH after reboot (timeout={timeout}s)")
        return False

    def run_post_install_features(self, topology_dir, ssh_config_file, bcm_version: str):
        """
        Execute post-install features defined in features.yaml
        
        Args:
            topology_dir: Path to topology directory
            ssh_config_file: Path to SSH config file for remote execution
            
        Returns:
            bool: True if all enabled features succeeded
        """
        print("\n" + "="*60)
        print("Post-Install Features")
        print("="*60)
        
        features = self.load_topology_features(topology_dir)
        if not features:
            print("  No features configured")
            return True
        
        topology_dir = Path(topology_dir)
        success = True
        bcm_major = (bcm_version.split(".")[0] if bcm_version else "").strip() or "10"

        enabled_features = []
        
        # Check which features are enabled
        for feature_name, config in features.items():
            if feature_name == "actions":
                continue
            if isinstance(config, dict) and config.get('enabled', False):
                enabled_features.append((feature_name, config))
        
        if not enabled_features:
            print("  All features disabled in features.yaml")
            return True
        
        # Build an ordered action list. If features.yaml contains an explicit "actions:" list, use it.
        # Otherwise, run enabled features in YAML order, with optional per-feature reboot_after.
        actions = []

        explicit_actions = features.get("actions")
        if isinstance(explicit_actions, list):
            actions = explicit_actions
        else:
            for feature_name, config in enabled_features:
                config_file = self._resolve_versioned_value(config.get("config_file"), bcm_major)
                if config_file:
                    actions.append({"type": "cmsh", "name": feature_name, "script": str(config_file)})
                if feature_name == "bcm_switches":
                    ztp_script = self._resolve_versioned_value(config.get("ztp_script"), bcm_major)
                    if ztp_script:
                        actions.insert(len(actions) - 1, {"type": "upload_ztp", "name": feature_name, "path": str(ztp_script)})
                if config.get("reboot_after", False):
                    actions.append({"type": "reboot", "name": f"{feature_name}-reboot"})

        if not actions:
            print("  No post-install actions to run")
            return True

        # Resumable execution using progress.json
        progress = getattr(self, "progress", None)
        idx = 0
        if progress:
            idx = int(progress.get("post_install_action_index", 0) or 0)
            progress.set(post_install_action_total=len(actions))

        print(f"  Planned actions: {len(actions)}")

        for i in range(idx, len(actions)):
            act = actions[i]
            act_type = (act.get("type") or "").strip()
            act_name = act.get("name") or f"action-{i+1}"
            if progress:
                progress.set(post_install_action_index=i, post_install_action=act)

            print(f"\n  [{i+1}/{len(actions)}] {act_type}: {act_name}")

            if act_type == "cmsh":
                script_val = self._resolve_versioned_value(act.get("script"), bcm_major)
                if not script_val:
                    print("    ⚠ Missing script for cmsh action")
                    success = False
                    break
                local_path = topology_dir / str(script_val)
                if not local_path.exists():
                    print(f"    ⚠ Config file not found: {local_path}")
                    success = False
                    break
                success = self._run_cmsh_script(local_path, ssh_config_file) and success
                if not success:
                    break
            elif act_type == "upload_ztp":
                path_val = self._resolve_versioned_value(act.get("path"), bcm_major)
                if path_val:
                    ztp_path = topology_dir / str(path_val)
                    if ztp_path.exists():
                        success = self._upload_ztp_script(ztp_path, ssh_config_file) and success
                    else:
                        print(f"    ⚠ ZTP script not found: {ztp_path}")
                else:
                    print("    ⚠ Missing path for upload_ztp action")
            elif act_type == "reboot":
                ok = self._managed_reboot(ssh_config_file)
                success = ok and success
                if not ok:
                    break
            elif act_type == "wlm_setup":
                # Optional explicit action type (kept for future expansions)
                cfg = self._resolve_versioned_value(act.get("config"), bcm_major)
                if not cfg:
                    print("    ⚠ Missing config for wlm_setup action")
                    success = False
                    break
                local_path = topology_dir / str(cfg)
                wlm_type = act.get("wlm_type") or "slurm"
                success = self._run_wlm_setup(local_path, ssh_config_file, wlm_type) and success
                if not success:
                    break
            else:
                print(f"    ⚠ Unknown action type: {act_type} (skipping)")

            if progress:
                progress.set(post_install_action_index=i + 1)
        
        if success:
            print("\n  ✓ All features configured successfully")
        else:
            print("\n  ⚠ Some features had errors (see above)")
        
        return success
    
    def _run_cmsh_script(self, local_script_path, ssh_config_file):
        """Upload and execute a cmsh script on the BCM head node"""
        remote_script = f"/tmp/{local_script_path.name}"
        
        try:
            # Upload script
            subprocess.run([
                'scp', '-F', ssh_config_file,
                str(local_script_path),
                f"air-{self.bcm_node_name}:{remote_script}"
            ], check=True, capture_output=True)
            
            # Execute with cmsh
            result = subprocess.run([
                'ssh', '-F', ssh_config_file,
                f"air-{self.bcm_node_name}",
                f"cmsh -f {remote_script}"
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                print(f"    ✓ {local_script_path.name} executed")
                return True
            else:
                print(f"    ✗ {local_script_path.name} failed: {result.stderr}")
                return False
        except subprocess.CalledProcessError as e:
            print(f"    ✗ Error running {local_script_path.name}: {e}")
            return False
    
    def _upload_ztp_script(self, local_ztp_path, ssh_config_file):
        """Upload ZTP script to BCM's HTTP directory for switch provisioning"""
        remote_ztp = "/cm/images/default-image/http/cumulus-ztp.sh"
        
        try:
            # Upload to temporary location first, then move with sudo
            subprocess.run([
                'scp', '-F', ssh_config_file,
                str(local_ztp_path),
                f"air-{self.bcm_node_name}:/tmp/cumulus-ztp.sh"
            ], check=True, capture_output=True)
            
            subprocess.run([
                'ssh', '-F', ssh_config_file,
                f"air-{self.bcm_node_name}",
                f"sudo mkdir -p /cm/images/default-image/http && sudo mv /tmp/cumulus-ztp.sh {remote_ztp} && sudo chmod 644 {remote_ztp}"
            ], check=True, capture_output=True)
            
            print(f"    ✓ ZTP script uploaded to {remote_ztp}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"    ⚠ ZTP script upload failed: {e}")
            return False
    
    def _run_wlm_setup(self, config_path, ssh_config_file, wlm_type):
        """Run cm-wlm-setup with the provided configuration"""
        print(f"    Running cm-wlm-setup for {wlm_type}...")
        
        try:
            # Upload config file
            remote_config = f"/tmp/{config_path.name}"
            subprocess.run([
                'scp', '-F', ssh_config_file,
                str(config_path),
                f"air-{self.bcm_node_name}:{remote_config}"
            ], check=True, capture_output=True)
            
            # Run cm-wlm-setup
            result = subprocess.run([
                'ssh', '-F', ssh_config_file,
                f"air-{self.bcm_node_name}",
                f"sudo cm-wlm-setup -c {remote_config}"
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                print(f"    ✓ Workload manager ({wlm_type}) configured")
                return True
            else:
                print(f"    ⚠ cm-wlm-setup returned non-zero: {result.stderr}")
                return False
        except subprocess.CalledProcessError as e:
            print(f"    ✗ Error running cm-wlm-setup: {e}")
            return False


def _command_exists(cmd: str) -> bool:
    try:
        return subprocess.run(["which", cmd], capture_output=True).returncode == 0
    except Exception:
        return False


def _strip_flag_args(argv: list[str], flags: set[str]) -> list[str]:
    """Remove any occurrences of the provided flags (boolean flags only)."""
    out: list[str] = []
    for a in argv:
        if a in flags:
            continue
        out.append(a)
    return out


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
        dest='topology_path',
        default='topologies/default',
        help='Path to topology directory (containing topology.json and features.yaml) or legacy JSON file. (default: topologies/default)'
    )
    parser.add_argument(
        '--sim-id',
        help='Use an existing simulation ID instead of creating a new simulation from a topology.'
    )
    parser.add_argument(
        '--primary',
        metavar='HOSTNAME',
        help='Primary BCM node hostname (used with --sim-id). If omitted, deploy_bcm_air.py will try to select one based on node names.'
    )
    parser.add_argument(
        '--secondary',
        metavar='HOSTNAME',
        help='Secondary BCM node hostname for HA install (used with --sim-id). No auto-detection is performed.'
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
        '--keep-progress',
        action='store_true',
        help='Do not auto-clear .logs/progress.json on successful completion (useful for test-loop logging).'
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
    parser.add_argument(
        '--skip-cloud-init',
        action='store_true',
        help='Skip applying cloud-init UserConfig to nodes (isolation/debug)'
    )
    parser.add_argument(
        '--skip-ssh-service',
        action='store_true',
        help='Skip creating the SSH service (isolation/debug)'
    )
    parser.add_argument(
        '--no-sdk',
        action='store_true',
        help='Do not use air-sdk even if installed; use REST-only (isolation/debug)'
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
                progress_tracker=progress,
                skip_cloud_init=args.skip_cloud_init,
                skip_ssh_service=args.skip_ssh_service,
                no_sdk=args.no_sdk,
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
        elif args.sim_id:
            # Existing simulation: name is fetched later from the API (best-effort).
            simulation_name = f"sim-{args.sim_id[:8]}"
            print(f"\nUsing existing simulation: {args.sim_id}")
            progress.complete_step('simulation_name_set', simulation_name=simulation_name)
        else:
            simulation_name = deployer.prompt_simulation_name()
            progress.complete_step('simulation_name_set', simulation_name=simulation_name)
        
        # Resolve topology path (supports directories or legacy JSON files)
        topology_path = Path(args.topology_path)
        if topology_path.is_dir():
            # New structure: topologies/default/ with topology.json inside
            topology_dir = topology_path
            topology_file = topology_path / 'topology.json'
        elif topology_path.suffix == '.json' and topology_path.exists():
            # Legacy: direct path to JSON file
            topology_file = topology_path
            topology_dir = topology_path.parent
        else:
            # Try adding .json extension for backward compatibility
            if (topology_path.parent / f"{topology_path.name}.json").exists():
                topology_file = topology_path.parent / f"{topology_path.name}.json"
                topology_dir = topology_path.parent
            else:
                if args.sim_id:
                    # In existing-sim mode, topology is optional (used only for optional post-install features).
                    topology_file = None
                    topology_dir = topology_path.parent
                else:
                    print(f"\n✗ Error: Topology not found: {topology_path}")
                    print("  Expected: directory with topology.json or a .json file")
                    sys.exit(1)

        # Validate existing-sim flags
        if args.secondary and not args.sim_id:
            print("\n✗ Error: --secondary requires --sim-id")
            return 2
        if args.primary and not args.sim_id:
            print("\n✗ Error: --primary requires --sim-id")
            return 2
        if args.primary and args.secondary:
            print("\n✗ Error: Use only one of --primary or --secondary")
            return 2
        
        # Step: Create simulation
        if args.resume and progress.is_step_completed('simulation_created'):
            deployer.simulation_id = progress.get('simulation_id')
            deployer.bcm_node_name = progress.get('bcm_node_name', 'bcm-01')
            deployer.bcm_outbound_interface = progress.get('bcm_outbound_interface', 'eth0')
            deployer.bcm_management_interface = progress.get('bcm_management_interface', 'eth1')
            deployer.bcm_internalnet_interface = progress.get('bcm_internalnet_interface', deployer.bcm_management_interface)
            deployer.bcm_internalnet_base = progress.get('bcm_internalnet_base')
            deployer.bcm_internalnet_prefixlen = progress.get('bcm_internalnet_prefixlen')
            deployer.bcm_internalnet_ip_primary = progress.get('bcm_internalnet_ip_primary')
            deployer.bcm_internalnet_ip_secondary = progress.get('bcm_internalnet_ip_secondary')
            deployer.userconfig_id = progress.get('userconfig_id')
            print(f"  [resume] Simulation ID: {deployer.simulation_id}")
            print(f"  [resume] BCM outbound interface: {deployer.bcm_outbound_interface}")
            print(f"  [resume] BCM internalnet interface: {deployer.bcm_management_interface}")
            if deployer.userconfig_id:
                print(f"  [resume] UserConfig ID: {deployer.userconfig_id}")
        else:
            # Step: Ensure UserConfig exists BEFORE simulation (avoids rate limiting)
            # UserConfigs are user-level, not simulation-specific
            userconfig_id = deployer.ensure_userconfig()

            if args.sim_id:
                deployer.simulation_id = args.sim_id

                # Pull simulation name from API (best-effort)
                try:
                    r = requests.get(
                        f"{api_base_url.rstrip('/')}/api/v2/simulations/{deployer.simulation_id}/",
                        headers=deployer.headers,
                        timeout=30,
                    )
                    if r.status_code == 200:
                        sj = r.json()
                        simulation_name = sj.get("title") or sj.get("name") or simulation_name
                except Exception:
                    pass

                # Determine target node and install role
                if args.secondary:
                    target_node = args.secondary
                    install_role = "secondary"
                elif args.primary:
                    target_node = args.primary
                    install_role = "primary"
                else:
                    # Heuristic selection for primary only
                    resp = requests.get(
                        f"{api_base_url.rstrip('/')}/api/v2/simulations/nodes/",
                        headers=deployer.headers,
                        params={"simulation": deployer.simulation_id},
                        timeout=30,
                    )
                    nodes = []
                    if resp.status_code == 200:
                        data = resp.json()
                        nodes = data.get("results", data) if isinstance(data, dict) else data
                    names = [n.get("name") for n in nodes if isinstance(n, dict) and isinstance(n.get("name"), str)]
                    bcmish = [n for n in names if "bcm" in n.lower()]
                    starts = [n for n in bcmish if n.lower().startswith("bcm")]
                    candidates = starts or bcmish
                    ends1 = [n for n in candidates if re.search(r"1$", n)]
                    candidates = ends1 or candidates

                    if deployer.non_interactive:
                        if len(candidates) == 1:
                            target_node = candidates[0]
                            print(f"\n  [non-interactive] Using BCM node: {target_node}")
                        else:
                            print("\n✗ Could not uniquely determine BCM node in --sim-id mode.")
                            print("  Please re-run with --primary <hostname>.")
                            return 2
                    else:
                        if len(candidates) == 1:
                            target_node = candidates[0]
                            print(f"\n  ✓ Selected BCM node: {target_node}")
                        elif len(candidates) > 1:
                            print("\nMultiple BCM-like nodes found:")
                            for i, n in enumerate(candidates, start=1):
                                print(f"  {i}) {n}")
                            choice = input("Select primary BCM node by number: ").strip()
                            try:
                                idx = int(choice)
                                target_node = candidates[idx - 1]
                            except Exception:
                                print("✗ Invalid selection; re-run with --primary <hostname>")
                                return 2
                        else:
                            target_node = input("Enter primary BCM node hostname: ").strip()
                            if not target_node:
                                print("✗ No hostname provided.")
                                return 2
                    install_role = "primary"

                deployer.bcm_node_name = target_node
                deployer.bcm_outbound_interface = "eth0"  # requirement unchanged

                # Derive internalnet params and pick interface without a topology file
                deployer._derive_internalnet_params()
                if deployer.bcm_internalnet_if_override:
                    deployer.bcm_management_interface = deployer.bcm_internalnet_if_override
                else:
                    ifaces = deployer._list_node_interfaces_from_api(deployer.simulation_id, deployer.bcm_node_name)
                    chosen = deployer._select_internalnet_interface_from_interfaces(ifaces) or "eth1"
                    deployer.bcm_management_interface = chosen
                deployer.bcm_internalnet_interface = deployer.bcm_management_interface

                # Primary vs secondary internalnet IP for install
                if install_role == "secondary":
                    deployer.bcm_internalnet_ip_primary = deployer.bcm_internalnet_ip_secondary

                progress.complete_step(
                    'simulation_created',
                    simulation_id=deployer.simulation_id,
                    simulation_name=simulation_name,
                    bcm_node_name=deployer.bcm_node_name,
                    bcm_outbound_interface=deployer.bcm_outbound_interface,
                    bcm_management_interface=deployer.bcm_management_interface,
                    bcm_internalnet_interface=deployer.bcm_internalnet_interface,
                    bcm_internalnet_base=deployer.bcm_internalnet_base,
                    bcm_internalnet_prefixlen=deployer.bcm_internalnet_prefixlen,
                    bcm_internalnet_ip_primary=deployer.bcm_internalnet_ip_primary,
                    bcm_internalnet_ip_secondary=deployer.bcm_internalnet_ip_secondary,
                    userconfig_id=userconfig_id,
                    topology_dir=str(topology_dir),
                    existing_sim=True,
                    install_role=install_role,
                )
            else:
                if not topology_file or not topology_file.exists():
                    print(f"\n✗ Error: Topology file not found: {topology_file}")
                    sys.exit(1)
                deployer.create_simulation(topology_file, simulation_name)
                progress.complete_step('simulation_created', 
                                       simulation_id=deployer.simulation_id,
                                       simulation_name=simulation_name,
                                       bcm_node_name=deployer.bcm_node_name,
                                       bcm_outbound_interface=deployer.bcm_outbound_interface,
                                       bcm_management_interface=deployer.bcm_management_interface,
                                       bcm_internalnet_interface=deployer.bcm_internalnet_interface,
                                       bcm_internalnet_base=deployer.bcm_internalnet_base,
                                       bcm_internalnet_prefixlen=deployer.bcm_internalnet_prefixlen,
                                       bcm_internalnet_ip_primary=deployer.bcm_internalnet_ip_primary,
                                       bcm_internalnet_ip_secondary=deployer.bcm_internalnet_ip_secondary,
                                       userconfig_id=userconfig_id,
                                       topology_dir=str(topology_dir))
        
        # Step: Assign cloud-init to nodes (needs simulation to exist)
        if args.resume and progress.is_step_completed('cloudinit_configured'):
            cloudinit_success = progress.get('cloudinit_success', False)
            deployer.userconfig_id = progress.get('userconfig_id')
            print(f"  [resume] Cloud-init configured: {cloudinit_success}")
        else:
            cloudinit_success = deployer.configure_node_passwords_cloudinit()
            # In --sim-id mode, cloud-init assignment is unlikely to take effect unless nodes reboot/rebuild.
            # We still attempt assignment (it is cheap), but we should not treat it as "passwords configured"
            # when the simulation is already running.
            if args.sim_id:
                state = deployer.get_simulation_state()
                if state and str(state).upper() in ("LOADED", "RUNNING") and cloudinit_success and not args.skip_cloud_init:
                    print("\nℹ Simulation is already running/loaded; cloud-init changes will not apply until nodes reboot/rebuild.")
                    print("ℹ Falling back to SSH bootstrap for this run (or re-run with --skip-cloud-init).")
                    cloudinit_success = False
            progress.complete_step('cloudinit_configured', cloudinit_success=cloudinit_success)
        
        # Step: Start simulation
        if args.resume and progress.is_step_completed('simulation_started'):
            print(f"  [resume] Simulation already started")
        else:
            # In --sim-id mode, the simulation may already be LOADED/RUNNING.
            # Starting in that state can return 400 ("must be NEW/STORED/ERROR/SNAPSHOT").
            state = deployer.get_simulation_state()
            progress.set(simulation_state_before_start=state)
            if args.sim_id and state and str(state).upper() in ("LOADED", "RUNNING"):
                print(f"\nℹ Simulation already {state}; skipping start/load steps.")
                progress.complete_step('simulation_started')
                progress.complete_step('simulation_loaded')
            else:
                deployer.start_simulation()
                progress.complete_step('simulation_started')
        
        # Step: Wait for simulation loaded
        if args.resume and progress.is_step_completed('simulation_loaded'):
            print(f"  [resume] Simulation already loaded")
        else:
            if not deployer.wait_for_simulation_loaded(timeout=300):
                print("\n✗ Error: Simulation did not load in time")

                # Auto-fallback: if cloud-init was used and sim fails to load, retry once with --skip-cloud-init
                # (requires sshpass or expect for the SSH/password bootstrap path).
                if (not args.skip_cloud_init) and cloudinit_success:
                    have_expect = _command_exists("expect")
                    have_sshpass = _command_exists("sshpass")

                    if not (have_expect or have_sshpass):
                        print("\nSimulation failed to load with cloud-init.")
                        print('Please run "sudo apt update && sudo apt install expect" and then try again with "./deploy_bcm_air.py --skip-cloud-init".')
                        print("Tip: sshpass also works (sudo apt install sshpass).")
                        return 1

                    print("\nSimulation failed to load with cloud-init; falling back to SSH/expect method.")
                    print("Deleting simulation and restarting from the beginning with --skip-cloud-init...")

                    # Delete current sim (best-effort)
                    deleted = deployer.delete_simulation()
                    if deleted:
                        print("  ✓ Simulation deleted")
                    else:
                        print("  ⚠ Simulation delete failed (continuing anyway)")

                    # Clear progress so we truly restart from the beginning
                    progress.clear()

                    # Re-run this script with the same args, forcing skip-cloud-init
                    rerun_args = _strip_flag_args(sys.argv[1:], {"--resume"})
                    if "--skip-cloud-init" not in rerun_args:
                        rerun_args.append("--skip-cloud-init")
                    # IMPORTANT: do NOT pass --clear-progress here. That flag is designed to
                    # clear progress and exit cleanly. We already called progress.clear()
                    # above, so the rerun will start fresh automatically.

                    print(f"\n[auto-fallback] Re-running: {sys.executable} {Path(__file__).name} {' '.join(rerun_args)}")
                    rc = subprocess.run([sys.executable, str(Path(__file__).resolve()), *rerun_args]).returncode
                    return rc

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
        
        ssh_info = deployer.get_ssh_service_info(interface=getattr(deployer, "bcm_outbound_interface", "eth0"))
        
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
            print("\n⚠ Cloud-init configuration failed")
            print("  Note: On air.nvidia.com free tier, cloud-init may not be available")
            print("  Attempting SSH fallback for password configuration...")
            ok, details = deployer.configure_node_passwords(ssh_info)
            if not ok:
                print("\n  ℹ Continuing with default password 'nvidia'")
                print("  ℹ You can change it manually after connecting")
            progress.set(
                bootstrap_method=details.get("bootstrap_method", "ssh-unknown"),
                bootstrap_tool=details.get("bootstrap_tool"),
                bootstrap_password_change_prompt=details.get("password_change_prompt"),
            )
        else:
            print("\n✓ Passwords configured via cloud-init (set at boot time)")
            progress.set(
                bootstrap_method="cloud-init",
                bootstrap_tool="cloud-init",
                bootstrap_password_change_prompt=None,
            )
        
        # Step: Install BCM
        if not args.skip_ansible:
            if args.resume and progress.is_step_completed('bcm_installed'):
                print(f"  [resume] BCM already installed")
            else:
                deployer.install_bcm(bcm_version, ssh_config_file, bcm_iso_path)
                progress.complete_step('bcm_installed')
        else:
            print("\n--skip-ansible specified, skipping BCM installation")
        
        # Step: Post-install features (if features.yaml exists in topology directory)
        if not args.skip_ansible:
            if args.resume and progress.is_step_completed('features_configured'):
                print(f"  [resume] Features already configured")
            else:
                # Get topology_dir from progress or current resolution
                saved_topology_dir = progress.get('topology_dir')
                feature_topology_dir = Path(saved_topology_dir) if saved_topology_dir else topology_dir
                
                deployer.run_post_install_features(feature_topology_dir, ssh_config_file, bcm_version=bcm_version)
                progress.complete_step('features_configured')
        
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
            if args.keep_progress:
                print("  [non-interactive] Keeping progress file (--keep-progress)")
            else:
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

