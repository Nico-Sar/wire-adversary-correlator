#!/usr/bin/env bash
# scripts/fix_nym2_routing.sh
# ============================
# Makes the nym2 eth0 default-route deletion persistent across reboots.
#
# Problem: `ip route del default via 172.31.1.1 dev eth0` is not persistent.
# After a reboot the eth0 default route returns and nym2 traffic bypasses the
# ingress router, breaking direction-assignment in pcap analysis.
#
# Fix: installs a one-shot systemd service on each nym2 client VM that runs
# the route deletion at every boot (After=network.target).
#
# Targets:
#   nym2-client1  204.168.181.115  (private IP 10.0.0.4)
#   nym2-client2  95.216.218.124   (private IP 10.0.0.6)
#
# Usage (from repo root):
#   bash scripts/fix_nym2_routing.sh

set -euo pipefail

SSH_KEY="$HOME/.ssh/nico-thesis"
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=15 -i $SSH_KEY"

NYM2_VMS=(
    "root@204.168.181.115:nym2-client1"
    "root@95.216.218.124:nym2-client2"
)

SERVICE_NAME="nym2-routing-fix"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

SERVICE_CONTENT="[Unit]
Description=Delete eth0 default route so traffic routes through ingress router
After=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'ip route del default via 172.31.1.1 dev eth0 2>/dev/null || true'

[Install]
WantedBy=multi-user.target
"

for entry in "${NYM2_VMS[@]}"; do
    target="${entry%%:*}"   # root@<ip>
    label="${entry##*:}"    # nym2-client1 / nym2-client2

    echo ""
    echo "══════════════════════════════════════════════════"
    echo " Applying fix to $label ($target)"
    echo "══════════════════════════════════════════════════"

    # Write systemd service file
    ssh $SSH_OPTS "$target" "cat > $SERVICE_FILE" <<EOF
$SERVICE_CONTENT
EOF
    echo "  [ok] wrote $SERVICE_FILE"

    # Reload daemon, enable and start the service
    ssh $SSH_OPTS "$target" "
        systemctl daemon-reload
        systemctl enable  $SERVICE_NAME
        systemctl restart $SERVICE_NAME
    "
    echo "  [ok] service enabled and started"

    # Verify the route is now absent
    echo "  Checking ip route..."
    result=$(ssh $SSH_OPTS "$target" "ip route show | grep 'default.*eth0' || true")
    if [[ -z "$result" ]]; then
        echo "  [PASS] default via eth0 route is absent"
    else
        echo "  [FAIL] default via eth0 route still present: $result"
        exit 1
    fi

    # Show the full default-route table for confirmation
    echo "  Current default routes:"
    ssh $SSH_OPTS "$target" "ip route show | grep default || echo '  (none)'" | sed 's/^/    /'
done

echo ""
echo "══════════════════════════════════════════════════"
echo " fix_nym2_routing.sh complete — both VMs fixed."
echo "══════════════════════════════════════════════════"
