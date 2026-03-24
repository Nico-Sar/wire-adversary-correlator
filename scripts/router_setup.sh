#!/bin/bash
# scripts/router_setup.sh
# ========================
# Bootstrap an Ubuntu 22.04 Hetzner VM as an adversary router.
# Run this on BOTH the ingress and egress router VMs.
#
# Verified for:
#   - ubuntu-4gb-hel1-6  (ingress, 204.168.184.30, 10.0.0.2)
#   - ubuntu-4gb-hel1-8  (egress,  204.168.189.97, 10.0.0.3)
#
# Interfaces (verified from ip addr show):
#   eth0    = public internet
#   enp7s0  = private VPC (10.0.0.x)
#
# Usage:
#   bash scripts/router_setup.sh

set -euo pipefail

PUBLIC_IFACE="eth0"
PRIVATE_IFACE="enp7s0"
CAPTURE_DIR="/tmp/captures"

echo "=============================="
echo " Wire Adversary Router Setup"
echo " Host: $(hostname)"
echo " Public:  $(ip -4 addr show $PUBLIC_IFACE | grep -oP '(?<=inet\s)\d+(\.\d+){3}')"
echo " Private: $(ip -4 addr show $PRIVATE_IFACE | grep -oP '(?<=inet\s)\d+(\.\d+){3}')"
echo "=============================="

# ── 1. System update and packages ─────────────────────────────────────────────
echo "[1/5] Installing packages..."
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    tshark \
    chrony \
    tcpdump \
    net-tools \
    iproute2

# Allow tshark without root
echo "wireshark-common wireshark-common/install-setuid boolean true" \
    | debconf-set-selections
DEBIAN_FRONTEND=noninteractive dpkg-reconfigure -f noninteractive wireshark-common
usermod -aG wireshark root

# ── 2. IP forwarding ──────────────────────────────────────────────────────────
echo "[2/5] Enabling IP forwarding..."
cat > /etc/sysctl.d/99-router.conf << 'EOF'
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
EOF
sysctl -p /etc/sysctl.d/99-router.conf

# ── 3. Chrony (clock sync) ────────────────────────────────────────────────────
echo "[3/5] Configuring chrony..."
cat > /etc/chrony/chrony.conf << 'EOF'
# High-quality time sources for sub-millisecond sync
server time.cloudflare.com iburst
server 0.pool.ntp.org iburst
server 1.pool.ntp.org iburst

# Step clock on startup if needed
makestep 1 3

# Keep real-time clock in sync
rtcsync

# Log tracking data
logdir /var/log/chrony
log tracking measurements statistics
EOF

systemctl enable chrony
systemctl restart chrony

echo "    Waiting 5s for chrony to sync..."
sleep 5
chronyc tracking | grep -E "System time|Stratum|Reference"

# ── 4. Capture directory ──────────────────────────────────────────────────────
echo "[4/5] Creating capture directory..."
mkdir -p $CAPTURE_DIR
chmod 1777 $CAPTURE_DIR

# ── 5. Verify tshark works ────────────────────────────────────────────────────
echo "[5/5] Verifying tshark..."
tshark --version | head -1

echo ""
echo "=============================="
echo " Setup complete: $(hostname)"
echo " Capture dir: $CAPTURE_DIR"
echo " IP forwarding: $(sysctl net.ipv4.ip_forward | awk '{print $3}')"
echo " Chrony stratum: $(chronyc tracking | grep Stratum | awk '{print $3}')"
echo " Run 'chronyc tracking' to verify clock sync"
echo "=============================="