#!/usr/bin/env python3
"""
Test script to verify Air SDK authentication with API tokens
"""

import os
import sys
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

try:
    from air_sdk.v2 import AirApi
    print("✓ air_sdk package found")
except ImportError:
    print("✗ air_sdk not installed")
    print("\nInstall it with:")
    print("  uv pip install air-sdk")
    sys.exit(1)

# Get credentials
api_token = os.getenv('AIR_API_TOKEN')
username = os.getenv('AIR_USERNAME', 'travisw@nvidia.com')  # Default username
api_url = os.getenv('AIR_API_URL', 'https://air.nvidia.com')

# Remove /api/v2 suffix if present - SDK adds this automatically
if api_url.endswith('/api/v2'):
    api_url = api_url[:-7]
if api_url.endswith('/api/v1'):
    api_url = api_url[:-7]

if not api_token:
    print("✗ AIR_API_TOKEN not set")
    print("\nRun: export AIR_API_TOKEN=your_token_here")
    sys.exit(1)

print(f"\nTesting Air SDK authentication...")
print(f"API Base URL: {api_url}")
print(f"Username: {username}")
print(f"Token: {api_token[:10]}...{api_token[-4:]}")
print()

# Test authentication with token as password
try:
    print("Attempting SDK initialization...")
    # The SDK uses username/password, but we can pass the API token as the password
    api = AirApi(
        api_url=api_url,
        username=username,
        password=api_token,  # Use API token as password
    )
    
    print("✓ SDK initialization successful!")
    print("\nTesting API access - listing simulations...")
    
    # Try listing simulations
    try:
        sims = list(api.simulations.list())
        print(f"✓ Successfully retrieved {len(sims)} simulation(s)")
        
        if sims:
            print(f"\nFirst simulation: {sims[0].title} (ID: {sims[0].id})")
        
        print("\n" + "="*60)
        print("SDK is working correctly!")
        print("="*60)
    except Exception as list_error:
        print(f"✗ Error listing simulations: {list_error}")
        print(f"Error type: {type(list_error).__name__}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
except Exception as e:
    print(f"✗ Error during SDK initialization: {e}")
    print(f"Error type: {type(e).__name__}")
    print("\nFull error details:")
    import traceback
    traceback.print_exc()
    print("\nTroubleshooting:")
    print("1. Verify AIR_USERNAME matches your Air account email")
    print("2. Verify AIR_API_TOKEN is valid for the specified API_URL")
    print("3. For internal Air, make sure you're on VPN")
    print("4. Try with base URL only (no /api/v2 suffix)")
    sys.exit(1)

