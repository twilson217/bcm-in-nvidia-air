## Ansible Installer (Local Head Node Install)

This repo already contains everything needed to install BCM using Bright’s Ansible playbook approach. For private environments (e.g., your own OpenStack), the simplest workflow is to **clone this repo directly on the target head node** and run the local installer wrapper.

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
sudo -E python3 ansible-installer/install-bcm.py \
  --iso /path/to/BCM-11.30.0.iso \
  --product-key '<YOUR_BCM_PRODUCT_KEY>' \
  --password '<YOUR_ADMIN_PASSWORD>' \
  --admin-email 'admin@example.com' \
  --external-interface ens3 \
  --management-interface ens4
```

### Single-NIC head node
If your head node only has **one NIC** (and you only want to use it for `internalnet`), you can run “single-NIC mode”.

In this mode we **do not create BCM `externalnet`** at all; only `internalnet` is configured on the one interface.

```bash
sudo -E python3 ansible-installer/install-bcm.py \
  --iso /path/to/BCM-11.30.0.iso \
  --product-key '<YOUR_BCM_PRODUCT_KEY>' \
  --password '<YOUR_ADMIN_PASSWORD>' \
  --management-interface ens3 \
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
sudo -E python3 ansible-installer/install-bcm.py \
  --iso /path/to/BCM-11.30.0.iso \
  --product-key '<YOUR_BCM_PRODUCT_KEY>' \
  --password '<YOUR_ADMIN_PASSWORD>' \
  --dry-run
```


