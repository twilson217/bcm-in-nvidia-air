## Local Installer (Head Node Install + Local Post-Install)

This repo already contains everything needed to install BCM using Bright’s Ansible playbook approach, **without NVIDIA Air API access**. For private environments (or Air sites that don’t provide API keys), the simplest workflow is to **clone this repo directly on the target head node** and run the local installer wrapper, then run the local post-install actions.

### Prerequisites
- **Ubuntu head node** with `sudo` access
- **BCM ISO file** available on the head node filesystem
- **Outbound internet access** (for `ansible-galaxy` collection download); for BCM 11.x we automatically pin `brightcomputing.installer110` to the correct minor series

### Steps
1. Clone the repo onto the head node:

```bash
git clone <this-repo>
cd bcm-in-nvidia-air
git switch v0.8.1
```

2. Run the installer wrapper (as root via `sudo`):

```bash
sudo -E python3 local-installer/install-bcm.py \
  --iso /path/to/BCM-11.30.0.iso \
  --product-key '<YOUR_BCM_PRODUCT_KEY>' \
  --password '<YOUR_ADMIN_PASSWORD>' \
  --admin-email 'admin@example.com' \
  --external-interface auto \
  --management-interface auto
```

By default, the script uses **auto-detection** for NIC names (works for both `eth*` and `ens*` naming). If you need to override:

```bash
sudo -E python3 local-installer/install-bcm.py \
  --iso /path/to/BCM-11.30.0.iso \
  --product-key '<YOUR_BCM_PRODUCT_KEY>' \
  --password '<YOUR_ADMIN_PASSWORD>' \
  --external-interface eth0 \
  --management-interface eth1
```

3. Run local post-install actions (optional, driven by your topology `features.yaml`):

```bash
sudo -E python3 local-installer/run-post-install.py \
  --topology topologies/preconfigured \
  --bcm-version 11.30.0
```

### Single-NIC head node
If your head node only has **one NIC** (and you only want to use it for `internalnet`), you can run “single-NIC mode”.

In this mode we **do not create BCM `externalnet`** at all; only `internalnet` is configured on the one interface.

```bash
sudo -E python3 local-installer/install-bcm.py \
  --iso /path/to/BCM-11.30.0.iso \
  --product-key '<YOUR_BCM_PRODUCT_KEY>' \
  --password '<YOUR_ADMIN_PASSWORD>' \
  --management-interface auto \
  --single-nic
```

Notes:
- The wrapper will (by default) create a **symlink** to your ISO at `/home/ubuntu/bcm.iso` (where our `scripts/bcm_install.sh` expects it). Use `--iso-mode copy` to copy instead.
- If `/home/ubuntu/bcm.iso` already exists and you want to replace it, add `--force`.
- For BCM 11.x, if the script cannot determine a safe Galaxy pin, the install will **fail fast** rather than silently using “latest”.
- If a per-version installer patch exists in this repo (e.g. `scripts/patches/11.31.0.py`), the wrapper will automatically stage it under `/home/ubuntu/bcm_patches/` so `bcm_install.sh` can apply it.

### Logs
- The main Ansible run logs to: `/home/ubuntu/ansible_bcm_install.log`

### Dry run

```bash
sudo -E python3 local-installer/install-bcm.py \
  --iso /path/to/BCM-11.30.0.iso \
  --product-key '<YOUR_BCM_PRODUCT_KEY>' \
  --password '<YOUR_ADMIN_PASSWORD>' \
  --dry-run
```

### What this does NOT automate (no Air API)
If you normally run a fully automated NVIDIA Air deployment, some steps are inherently API-driven (simulation node resets, switch resets, etc.). The local post-install runner will **skip** those API-only actions and will print a **manual checklist** (based on your `features.yaml`) so you can perform them in the Air UI or via whatever control plane you have.


