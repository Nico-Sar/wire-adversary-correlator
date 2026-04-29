#!/bin/bash
# scripts/update_nym_post_connect.sh
# 1. Writes /usr/local/bin/nym-post-connect.sh (nft rules + routing fix only, no SOCKS5).
# 2. Writes /etc/systemd/system/nym-vpnd.service.d/post-connect.conf with two
#    ExecStartPost lines: SOCKS5 configure at t+10s, nft rules at t+15s.
# 3. Runs systemctl daemon-reload on each VM.
#
# Usage:
#   bash scripts/update_nym_post_connect.sh

set -euo pipefail

SSH_KEY="$HOME/.ssh/nico-thesis"
EXIT_GW="2xU4CBE6QiiYt6EyBXSALwxkNvM7gqJfjHXaMkjiFmYW"

NYM_VMS=(
    "204.168.204.120"   # nym5-client1
    "204.168.201.84"    # nym5-client2
    "204.168.181.115"   # nym2-client1
    "95.216.218.124"    # nym2-client2
)

for IP in "${NYM_VMS[@]}"; do
    echo "→ Updating $IP …"
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "root@${IP}" \
        EXIT_GW="$EXIT_GW" \
        'bash -s' << 'REMOTE'

# ── 1. nym-post-connect.sh (nft rules + routing fix only) ────────────────────
cat > /usr/local/bin/nym-post-connect.sh << 'SCRIPT'
#!/bin/bash
sleep 3
REJECT_HANDLE=$(nft -a list chain inet nym output \
  | grep "reject # handle" \
  | grep -v "tcp dport 53" | grep -v "udp dport 53" \
  | tail -1 | grep -o "handle [0-9]*" | awk "{print \$2}")
nft insert rule inet nym output handle $REJECT_HANDLE tcp sport 22 accept
nft insert rule inet nym output handle $REJECT_HANDLE ip daddr 10.0.0.0/16 accept
nft insert rule inet nym output handle $REJECT_HANDLE oif "eth0" accept
nft add rule inet nym input tcp dport 22 accept
nft add rule inet nym input ip saddr 10.0.0.0/16 accept
echo "Nym post-connect nft rules applied"
/usr/local/bin/nym-routing-fix.sh
SCRIPT
chmod +x /usr/local/bin/nym-post-connect.sh
echo "  nym-post-connect.sh written"

# ── 2. systemd drop-in: SOCKS5 at t+10s, nft rules at t+15s ─────────────────
mkdir -p /etc/systemd/system/nym-vpnd.service.d
cat > /etc/systemd/system/nym-vpnd.service.d/post-connect.conf << CONF
[Service]
ExecStartPost=/bin/bash -c 'sleep 10 && nym-vpnc socks5 disable || true; sleep 1; nym-vpnc socks5 enable --socks5-address 127.0.0.1:1080 --exit-id ${EXIT_GW} || true'
ExecStartPost=/bin/bash -c 'sleep 15 && /usr/local/bin/nym-post-connect.sh || true'
CONF
echo "  post-connect.conf written"

# ── 3. Reload systemd ─────────────────────────────────────────────────────────
systemctl daemon-reload
echo "  daemon-reload done"

REMOTE
done

echo ""
echo "All 4 Nym VMs updated."
echo "Verify with:"
echo "  ssh -i ~/.ssh/nico-thesis root@<VM> cat /usr/local/bin/nym-post-connect.sh"
echo "  ssh -i ~/.ssh/nico-thesis root@<VM> cat /etc/systemd/system/nym-vpnd.service.d/post-connect.conf"
