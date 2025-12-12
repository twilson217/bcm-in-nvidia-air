#!/usr/bin/env python3
"""
Quick setup verification script for BCM Air deployment
Checks if all prerequisites are met before attempting deployment

Usage:
    python scripts/check_setup.py
"""

import os
import sys
import re
import shutil
import subprocess
from pathlib import Path

# Get project root (parent of scripts directory)
PROJECT_ROOT = Path(__file__).parent.parent


def ensure_setup_files_exist():
    """Create .iso directory and .env file if they don't exist"""
    iso_dir = PROJECT_ROOT / ".iso"
    env_file = PROJECT_ROOT / ".env"
    env_example = PROJECT_ROOT / "sample-configs" / "env.example"
    
    created_items = []
    
    # Create .iso directory if missing
    if not iso_dir.exists():
        iso_dir.mkdir(parents=True)
        created_items.append(f"Created directory: {iso_dir}")
    
    # Copy env.example to .env if .env is missing
    if not env_file.exists():
        if env_example.exists():
            shutil.copy(env_example, env_file)
            created_items.append(f"Created .env from {env_example}")
        else:
            created_items.append(f"WARNING: {env_example} not found, cannot create .env")
    
    return created_items


def load_env_file():
    """Load .env file manually (don't require python-dotenv for this script)"""
    env_file = PROJECT_ROOT / ".env"
    env_vars = {}
    
    if not env_file.exists():
        return env_vars
    
    with open(env_file, 'r') as f:
        for line in f:
            line = line.strip()
            # Skip comments and empty lines
            if not line or line.startswith('#'):
                continue
            # Parse KEY=VALUE
            if '=' in line:
                key, _, value = line.partition('=')
                key = key.strip()
                value = value.strip()
                # Remove quotes if present
                if (value.startswith('"') and value.endswith('"')) or \
                   (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]
                env_vars[key] = value
    
    return env_vars


def check_item(name, check_func, critical=True):
    """Check an item and print status"""
    try:
        result, message = check_func()
        status = "✓" if result else "✗"
        color = "\033[92m" if result else "\033[91m"
        reset = "\033[0m"
        level = "" if critical else " (optional)"
        print(f"{color}{status}{reset} {name}{level}: {message}")
        return result
    except Exception as e:
        print(f"✗ {name}: Error - {e}")
        return False


def check_python():
    """Check Python version"""
    version = sys.version_info
    if version.major >= 3 and version.minor >= 10:
        return True, f"Python {version.major}.{version.minor}.{version.micro}"
    return False, f"Python {version.major}.{version.minor} (need 3.10+)"


def check_env_variable(env_vars, var_name, is_secret=False, placeholder_values=None):
    """Check if an environment variable is set and not a placeholder"""
    value = env_vars.get(var_name, "")
    
    # Default placeholder values to check against
    if placeholder_values is None:
        placeholder_values = [
            "your_api_token_here",
            "your_email@nvidia.com", 
            "your_product_key_here",
            ""
        ]
    
    if not value:
        return False, "Not set"
    
    if value in placeholder_values:
        return False, "Still set to placeholder value"
    
    if is_secret:
        # Mask the value for display
        if len(value) > 12:
            masked = value[:4] + "..." + value[-4:]
        else:
            masked = "***"
        return True, f"Set ({masked})"
    else:
        return True, f"Set ({value})"


def check_ssh_key_exists(env_vars, var_name):
    """Check if SSH key file exists"""
    path = env_vars.get(var_name, "")
    
    if not path:
        return False, "Path not set in .env"
    
    # Expand ~ to home directory
    expanded_path = os.path.expanduser(path)
    
    if os.path.exists(expanded_path):
        return True, f"Found ({path})"
    else:
        return False, f"File not found: {path}"


def parse_bcm_iso_version(filename):
    """
    Parse BCM version from ISO filename.
    
    Examples:
        bcm-10.0-ubuntu2404.iso -> ('10', '10.0.0')
        bcm-10.30.0-ubuntu2404.iso -> ('10', '10.30.0')
        bcm-11.30.0-ubuntu2404.iso -> ('11', '11.30.0')
    
    Returns:
        tuple: (major_version, full_version) or (None, None) if not BCM
    """
    import re
    name_lower = filename.lower()
    
    if 'bcm' not in name_lower and 'bright' not in name_lower:
        return None, None
    
    # Pattern to extract version: bcm-10.30.0-xxx.iso or bcm-11.0-xxx.iso
    version_pattern = re.compile(r'bcm-?(10|11)\.?(\d+)?\.?(\d+)?', re.IGNORECASE)
    match = version_pattern.search(name_lower)
    
    if match:
        major = match.group(1)  # '10' or '11'
        minor = match.group(2) or '0'
        patch = match.group(3) or '0'
        full_version = f"{major}.{minor}.{patch}"
        return major, full_version
    
    return None, None


def check_bcm_iso():
    """Check for BCM 10 or 11 ISO in .iso directory"""
    iso_dir = PROJECT_ROOT / ".iso"
    
    if not iso_dir.exists():
        return False, ".iso directory not found"
    
    iso_files = list(iso_dir.glob("*.iso"))
    
    if not iso_files:
        return False, "No .iso files found in .iso/ directory"
    
    # Parse BCM ISOs and group by major version
    bcm10_isos = []
    bcm11_isos = []
    other_isos = []
    
    for iso_file in iso_files:
        major, full_version = parse_bcm_iso_version(iso_file.name)
        size_gb = iso_file.stat().st_size / (1024**3)
        
        if major == '10':
            bcm10_isos.append((iso_file.name, full_version, size_gb))
        elif major == '11':
            bcm11_isos.append((iso_file.name, full_version, size_gb))
        else:
            other_isos.append(iso_file.name)
    
    if not bcm10_isos and not bcm11_isos:
        if other_isos:
            return True, f"Found ISO(s) but version unclear: {', '.join(other_isos[:3])}"
        return False, "No BCM ISOs found"
    
    # Build detailed message
    parts = []
    
    if bcm10_isos:
        if len(bcm10_isos) == 1:
            name, ver, size = bcm10_isos[0]
            parts.append(f"BCM 10: {ver} ({size:.1f}GB)")
        else:
            versions = ", ".join([v for _, v, _ in bcm10_isos])
            parts.append(f"BCM 10: {len(bcm10_isos)} ISOs ({versions})")
    
    if bcm11_isos:
        if len(bcm11_isos) == 1:
            name, ver, size = bcm11_isos[0]
            parts.append(f"BCM 11: {ver} ({size:.1f}GB)")
        else:
            versions = ", ".join([v for _, v, _ in bcm11_isos])
            parts.append(f"BCM 11: {len(bcm11_isos)} ISOs ({versions})")
    
    return True, " | ".join(parts)


def check_uv():
    """Check if uv is installed"""
    try:
        result = subprocess.run(
            ['uv', '--version'],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, "Not found"
    except FileNotFoundError:
        return False, "Not installed (run: curl -LsSf https://astral.sh/uv/install.sh | sh)"
    except subprocess.TimeoutExpired:
        return False, "Timeout (command took >30s)"
    except Exception as e:
        return False, f"Check failed: {e}"


def check_venv():
    """Check if virtual environment is activated"""
    venv = os.getenv('VIRTUAL_ENV')
    if venv:
        return True, f"Active ({os.path.basename(venv)})"
    return False, "Not activated (run: source .venv/bin/activate)"


def main():
    print("\n" + "=" * 70)
    print("BCM Air Deployment - Setup Check")
    print("=" * 70 + "\n")
    
    # Ensure setup files exist
    created_items = ensure_setup_files_exist()
    if created_items:
        print("📁 Setup files created:")
        for item in created_items:
            print(f"   {item}")
        print()
    
    # Load environment variables
    env_vars = load_env_file()
    
    if not env_vars:
        print("⚠️  No .env file found or it's empty.")
        print("   Please edit .env with your configuration.\n")
    
    # Track results
    critical_passed = []
    optional_passed = []
    
    print("─" * 70)
    print("System Requirements")
    print("─" * 70)
    
    critical_passed.append(check_item("Python 3.10+", check_python))
    critical_passed.append(check_item("uv package manager", check_uv))
    optional_passed.append(check_item("Virtual environment", check_venv, critical=False))
    
    print()
    print("─" * 70)
    print("NVIDIA Air Configuration (.env)")
    print("─" * 70)
    
    critical_passed.append(check_item(
        "AIR_API_TOKEN",
        lambda: check_env_variable(env_vars, "AIR_API_TOKEN", is_secret=True)
    ))
    
    critical_passed.append(check_item(
        "AIR_USERNAME", 
        lambda: check_env_variable(env_vars, "AIR_USERNAME")
    ))
    
    optional_passed.append(check_item(
        "AIR_API_URL",
        lambda: check_env_variable(env_vars, "AIR_API_URL", placeholder_values=[""]),
        critical=False
    ))
    
    print()
    print("─" * 70)
    print("SSH Configuration (.env)")
    print("─" * 70)
    
    critical_passed.append(check_item(
        "SSH_PRIVATE_KEY",
        lambda: check_ssh_key_exists(env_vars, "SSH_PRIVATE_KEY")
    ))
    
    critical_passed.append(check_item(
        "SSH_PUBLIC_KEY",
        lambda: check_ssh_key_exists(env_vars, "SSH_PUBLIC_KEY")
    ))
    
    print()
    print("─" * 70)
    print("BCM Configuration (.env)")
    print("─" * 70)
    
    critical_passed.append(check_item(
        "BCM_PRODUCT_KEY",
        lambda: check_env_variable(env_vars, "BCM_PRODUCT_KEY", is_secret=True)
    ))
    
    optional_passed.append(check_item(
        "BCM_ADMIN_EMAIL",
        lambda: check_env_variable(env_vars, "BCM_ADMIN_EMAIL"),
        critical=False
    ))
    
    print()
    print("─" * 70)
    print("BCM ISO File (.iso/)")
    print("─" * 70)
    
    critical_passed.append(check_item(
        "BCM ISO (v10 or v11)",
        check_bcm_iso
    ))
    
    # Summary
    print()
    print("=" * 70)
    
    critical_ok = all(critical_passed)
    optional_ok = all(optional_passed)
    
    if critical_ok:
        print("✓ All critical requirements met! Ready to deploy.")
        print("\nRun: python deploy_bcm_air.py")
        if not optional_ok:
            print("\n⚠️  Some optional items need attention (see above).")
    else:
        print("✗ Some critical requirements are missing.")
        print("\nPlease fix the issues above before deploying:")
        print("  1. Edit .env with your configuration")
        print("  2. Place your BCM ISO in the .iso/ directory")
        print("  3. Ensure SSH keys exist at the configured paths")
        print("\nSee README.md for detailed setup instructions.")
        sys.exit(1)
    
    print("=" * 70 + "\n")


if __name__ == '__main__':
    main()
