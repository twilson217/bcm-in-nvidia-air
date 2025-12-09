#!/bin/bash
# Quick API authentication test for NVIDIA Air

# Check if token is set
if [ -z "$AIR_API_TOKEN" ]; then
    echo "Error: AIR_API_TOKEN not set"
    echo "Run: export AIR_API_TOKEN=your_token_here"
    exit 1
fi

# Determine which Air site to test
if [ "$1" == "--internal" ]; then
    API_URL="https://air-inside.nvidia.com/api/v2"
    SITE="Internal"
else
    API_URL="https://air.nvidia.com/api/v2"
    SITE="External"
fi

echo "Testing $SITE NVIDIA Air API authentication..."
echo "URL: $API_URL"
echo "Token: ${AIR_API_TOKEN:0:10}...${AIR_API_TOKEN: -4}"
echo ""

# Test the API (note: trailing slash is required!)
curl -L -v -X GET \
    -H "Authorization: Bearer $AIR_API_TOKEN" \
    -H "Content-Type: application/json" \
    "$API_URL/simulations/" \
    2>&1 | grep -E "(< HTTP|Authentication|detail|count|results)"

echo ""
echo "If you see 'HTTP/1.1 200' or a list of simulations, authentication works!"
echo "If you see '401' or '403', the token is invalid or wrong site."

