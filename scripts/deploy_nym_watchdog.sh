#!/usr/bin/env bash
# scripts/deploy_nym_watchdog.sh
# ==============================
# Copies nym_watchdog.sh to all 4 Nym VMs and installs it as a systemd service.
#
# Usage (from repo root):
#   bash scripts/deploy_nym_watchdog.sh

set -euo pipefail

SCRIPT_SRC="$(dirname "$0")/nym_watchdog.sh"
REMOTE_BIN="/usr/local/bin/nym_watchdog.sh"
SERVICE_NAME="nym-watchdog"
SSH_KEY="~/.ssh/nico-thesis"
SSH_OPTS="-i $SSH_KEY -o StrictHostKeyChecking=no -o ConnectTimeout=10"

NYM_VMS=(
    "204.168.204.120"   # nym5-client1
    "204.168.201.84"    # nym5-client2
    "204.168.181.115"   # nym2-client1
    "95.216.218.124"    # nym2-client2
)

SERVICE_UNIT="[Unit]
Description=Nym VPN watchdog — auto-reconnect on tunnel drop
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/nym_watchdog.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"

if [[ ! -f "$SCRIPT_SRC" ]]; then
    echo "[error] $SCRIPT_SRC not found. Run from repo root."
    exit 1
fi

for HOST in "${NYM_VMS[@]}"; do
    echo ""
    echo "── $HOST ──────────────────────────────────────────"

    echo "  copying $SCRIPT_SRC → $HOST:$REMOTE_BIN"
    scp $SSH_OPTS "$SCRIPT_SRC" "root@${HOST}:${REMOTE_BIN}"
    ssh $SSH_OPTS "root@${HOST}" "chmod +x ${REMOTE_BIN}"

    echo "  installing systemd unit ${SERVICE_NAME}.service"
    ssh $SSH_OPTS "root@${HOST}" "cat > /etc/systemd/system/${SERVICE_NAME}.service" <<EOF
$SERVICE_UNIT
EOF

    echo "  enabling and starting ${SERVICE_NAME}.service"
    ssh $SSH_OPTS "root@${HOST}" "
        systemctl daemon-reload
        systemctl enable  ${SERVICE_NAME}.service
        systemctl restart ${SERVICE_NAME}.service
        systemctl status  ${SERVICE_NAME}.service --no-pager -l
    "
    echo "  done."
done

echo ""
echo "========================================"
echo " Watchdog deployed to ${#NYM_VMS[@]} VMs."
echo "========================================"
