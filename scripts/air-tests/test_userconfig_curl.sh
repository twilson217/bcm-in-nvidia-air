#!/bin/bash
#
# Minimal curl reproduction for UserConfig API 403 issue on air.nvidia.com
#
# Issue: Free tier accounts get 403 "Access Denied" from Akamai CDN
# when trying to POST to /api/v2/userconfigs/
#
# Usage:
#   export AIR_USERNAME="your_email@example.com"
#   export AIR_API_TOKEN="your_token"
#   ./test_userconfig_curl.sh
#

set -e

API_URL="${AIR_API_URL:-https://air.nvidia.com}"

if [ -z "$AIR_USERNAME" ] || [ -z "$AIR_API_TOKEN" ]; then
    echo "ERROR: Set AIR_USERNAME and AIR_API_TOKEN environment variables"
    echo "  export AIR_USERNAME=your_email@example.com"
    echo "  export AIR_API_TOKEN=your_token"
    exit 1
fi

echo "API URL: $API_URL"
echo "Username: $AIR_USERNAME"
echo ""

# Step 1: Login
echo "============================================================"
echo "Step 1: Login to get JWT token"
echo "============================================================"
LOGIN_RESPONSE=$(curl -s -X POST "$API_URL/api/v1/login/" \
    -d "username=$AIR_USERNAME" \
    -d "password=$AIR_API_TOKEN")

JWT=$(echo "$LOGIN_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('token', ''))" 2>/dev/null || echo "")

if [ -z "$JWT" ]; then
    echo "Login failed!"
    echo "$LOGIN_RESPONSE"
    exit 1
fi
echo "✓ JWT token obtained"

# Step 2: GET UserConfigs (usually works)
echo ""
echo "============================================================"
echo "Step 2: GET /api/v2/userconfigs/"
echo "============================================================"
curl -s -w "\nHTTP Status: %{http_code}\n" \
    -H "Authorization: Bearer $JWT" \
    "$API_URL/api/v2/userconfigs/" | head -20

# Step 3: POST UserConfig (this fails with 403 for free tier)
echo ""
echo "============================================================"
echo "Step 3: POST /api/v2/userconfigs/ (this is what fails)"
echo "============================================================"
curl -s -w "\nHTTP Status: %{http_code}\n" \
    -H "Authorization: Bearer $JWT" \
    -H "Content-Type: application/json" \
    -X POST "$API_URL/api/v2/userconfigs/" \
    -d '{"name":"test-curl","kind":"cloud-init-user-data","organization":null,"content":"#cloud-config"}'

# Step 4: Control - GET simulations (should work)
echo ""
echo ""
echo "============================================================"
echo "Step 4: GET /api/v2/simulations/ (control - should work)"
echo "============================================================"
curl -s -w "\nHTTP Status: %{http_code}\n" \
    -H "Authorization: Bearer $JWT" \
    "$API_URL/api/v2/simulations/" | head -5

echo ""
echo ""
echo "============================================================"
echo "Summary"
echo "============================================================"
echo "If Step 3 returns 403 with 'Access Denied' HTML from Akamai,"
echo "the UserConfig API is blocked for this account tier."

