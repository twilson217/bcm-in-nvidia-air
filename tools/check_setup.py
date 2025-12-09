#!/usr/bin/env python3
"""
Quick setup verification script for BCM Air deployment
Checks if all prerequisites are met before attempting deployment
"""

import os
import sys
import subprocess
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def check_item(name, check_func):
    """Check an item and print status"""
    try:
        result, message = check_func()
        status = "✓" if result else "✗"
        color = "\033[92m" if result else "\033[91m"
        reset = "\033[0m"
        print(f"{color}{status}{reset} {name}: {message}")
        return result
    except Exception as e:
        print(f"✗ {name}: Error - {e}")
        return False

def check_python():
    """Check Python version"""
    version = sys.version_info
    if version.major >= 3 and version.minor >= 8:
        return True, f"Python {version.major}.{version.minor}.{version.micro}"
    return False, f"Python {version.major}.{version.minor} (need 3.8+)"

def check_api_token():
    """Check if AIR_API_TOKEN is set"""
    token = os.getenv('AIR_API_TOKEN')
    if token:
        masked = token[:8] + "..." + token[-4:] if len(token) > 12 else "***"
        return True, f"Set ({masked})"
    return False, "Not set (export AIR_API_TOKEN=your_token)"

def check_api_url():
    """Check AIR_API_URL setting"""
    url = os.getenv('AIR_API_URL', 'https://air.nvidia.com/api/v2')
    return True, url

def check_ansible():
    """Check if Ansible is installed"""
    try:
        result = subprocess.run(
            ['ansible', '--version'],
            capture_output=True,
            text=True,
            timeout=30  # Increased timeout for WSL environments
        )
        if result.returncode == 0:
            version_line = result.stdout.split('\n')[0]
            return True, version_line.split('[')[0].strip()
        return False, "Not found"
    except FileNotFoundError:
        return False, "Not installed"
    except subprocess.TimeoutExpired:
        return False, "Timeout (command took >30s - may still work)"
    except Exception as e:
        return False, f"Check failed: {e}"

def check_uv():
    """Check if uv is installed"""
    try:
        result = subprocess.run(
            ['uv', '--version'],
            capture_output=True,
            text=True,
            timeout=30  # Increased timeout for WSL environments
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, "Not found"
    except FileNotFoundError:
        return False, "Not installed (optional)"
    except subprocess.TimeoutExpired:
        return False, "Timeout (command took >30s - may still work)"
    except Exception as e:
        return False, f"Check failed: {e}"

def check_venv():
    """Check if virtual environment is activated"""
    venv = os.getenv('VIRTUAL_ENV')
    if venv:
        return True, f"Active ({os.path.basename(venv)})"
    return False, "Not activated (optional but recommended)"

def main():
    print("\n" + "="*60)
    print("BCM Air Deployment - Setup Check")
    print("="*60 + "\n")
    
    checks = [
        ("Python 3.8+", check_python),
        ("AIR_API_TOKEN", check_api_token),
        ("AIR_API_URL", check_api_url),
        ("Ansible", check_ansible),
        ("uv package manager", check_uv),
        ("Virtual environment", check_venv),
    ]
    
    results = []
    for name, check_func in checks:
        results.append(check_item(name, check_func))
    
    print("\n" + "="*60)
    
    critical_checks = results[:4]  # First 4 are critical
    
    if all(critical_checks):
        print("✓ All critical requirements met! Ready to deploy.")
        print("\nRun: python deploy_bcm_air.py")
    else:
        print("✗ Some requirements are missing.")
        print("\nPlease fix the issues above before deploying.")
        print("See README.md for setup instructions.")
        sys.exit(1)
    
    print("="*60 + "\n")

if __name__ == '__main__':
    main()

