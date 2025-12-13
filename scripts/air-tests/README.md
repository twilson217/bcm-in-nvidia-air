# NVIDIA Air API Test Scripts

This directory contains standalone scripts used for troubleshooting NVIDIA Air API issues. These are **not** part of the main deployment workflow—they were created to debug specific problems (authentication, UserConfig creation, WAF blocking, etc.) and are preserved here for future reference.

## When to Use These Scripts

Use these scripts when:
- The main deployment fails with authentication errors
- UserConfig creation returns 403 "Access Denied" 
- You need to reproduce an API issue for debugging
- You want to test specific API endpoints in isolation

## Prerequisites

All scripts require:
```bash
export AIR_API_TOKEN="your_token_here"
export AIR_USERNAME="your_email@example.com"
```

Or create a `.env` file in the repo root with these variables.

---

## Authentication Testing

### `test_auth.sh`
**Purpose**: Quick bash/curl test of NVIDIA Air API authentication.

```bash
./scripts/air-tests/test_auth.sh            # Test external Air
./scripts/air-tests/test_auth.sh --internal # Test internal Air (requires VPN)
```

### `test_sdk_auth.py`
**Purpose**: Test authentication using the Air SDK Python library.

```bash
python scripts/air-tests/test_sdk_auth.py
```

Tests if the SDK can authenticate and list simulations.

### `test_direct_auth.py`
**Purpose**: Test authentication using raw `requests` (bypasses SDK).

```bash
python scripts/air-tests/test_direct_auth.py
```

Useful when the SDK has issues—tests the `/api/v1/login/` endpoint directly.

---

## UserConfig API Testing

These scripts were created to debug a 403 "Access Denied" error from Akamai CDN when creating UserConfigs on `air.nvidia.com` (free tier). The issue was traced to the WAF blocking certain content patterns.

### `test_userconfig_api.py`
**Purpose**: Minimal reproduction script for UserConfig API issues.

```bash
python scripts/air-tests/test_userconfig_api.py
```

Tests:
- Login flow
- GET `/api/v2/userconfigs/` (list existing)
- POST `/api/v2/userconfigs/` (create new)

This is the script to share with NVIDIA Air developers when reporting issues.

### `test_userconfig_curl.sh`
**Purpose**: Same as above but using `curl` instead of Python.

```bash
./scripts/air-tests/test_userconfig_curl.sh
```

Useful for developers who prefer curl or need to debug outside Python.

### `test_userconfig_after_sim.py`
**Purpose**: Test if UserConfig creation timing matters.

```bash
python scripts/air-tests/test_userconfig_after_sim.py
```

Creates a minimal simulation, then immediately tries to create a UserConfig. Tests whether the 403 error is timing-related to simulation creation.

---

## WAF Pattern Testing

The Akamai CDN WAF on `air.nvidia.com` blocks certain content patterns in UserConfig payloads. These scripts helped identify the trigger patterns.

### `test_content_size.py`
**Purpose**: Test if content size triggers the WAF.

```bash
python scripts/air-tests/test_content_size.py
```

Creates UserConfigs with varying content sizes (10 bytes to 10KB) to rule out size limits.

### `test_waf_patterns.py`
**Purpose**: Test specific patterns that might trigger WAF.

```bash
python scripts/air-tests/test_waf_patterns.py
```

Tests patterns like:
- `/etc/ssh/` paths
- `sshd_config` references
- `PermitRootLogin` directives
- `write_files` with SSH paths

This identified that `/etc/ssh/` paths in cloud-init content trigger the WAF.

### `test_lines.py`
**Purpose**: Binary search to find the exact WAF trigger line.

```bash
python scripts/air-tests/test_lines.py
```

Tests each line of `cloud-init-password.yaml` individually to identify which specific line(s) trigger the WAF block.

### `test_actual_content.py`
**Purpose**: Test the actual cloud-init file content.

```bash
python scripts/air-tests/test_actual_content.py
```

Tests whether the current `cloud-init-password.yaml` file passes the WAF.

---

## Simulation State Debugging

### `check_sim_state.py`
**Purpose**: Debug script to inspect simulation state via API.

```bash
python scripts/air-tests/check_sim_state.py <simulation_id>
```

Shows:
- Simulation status (created, loading, loaded, etc.)
- Node states
- Service information

---

## Key Findings

These scripts helped discover:

1. **WAF Blocking**: The Akamai CDN on `air.nvidia.com` blocks UserConfig creation when the content contains `/etc/ssh/` paths (even in comments).

2. **Solution**: Use native cloud-init directives (`ssh_authorized_keys`, `disable_root: false`) instead of `write_files` or `runcmd` with SSH paths.

3. **Organization Field**: Free-tier accounts must send `"organization": null` explicitly in the JSON payload (not omit it).

