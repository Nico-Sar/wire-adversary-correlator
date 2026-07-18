#!/bin/bash
# scripts/update_nym_post_connect.sh
# 1. Writes /usr/local/bin/nym-post-connect.sh (nft rules + routing fix only, no SOCKS5).
# 2. Writes /etc/systemd/system/nym-vpnd.service.d/post-connect.conf with two
#    ExecStartPost lines: SOCKS5 configure at t+10s, nft rules at t+15s.
# 3. Runs systemctl daemon-reload on each VM.
#
# nym-post-connect.sh's last line calls /usr/local/bin/nym-ssh-routing-fix.sh,
# deployed separately by scripts/deploy_nym_ssh_routing_fix.sh — run that
# script first (or at least once) on any VM before this one, or the call at
# the end of nym-post-connect.sh will just no-op (file not found, ignored by
# the calling context but the SSH-survival rule won't get reasserted).
#
# Usage:
#   bash scripts/update_nym_post_connect.sh

set -euo pipefail

SSH_KEY="$HOME/.ssh/nico-thesis"
EXIT_GW="2xU4CBE6QiiYt6EyBXSALwxkNvM7gqJfjHXaMkjiFmYW"

# Keep in sync with config/infrastructure.py's CLIENTS dict — this list was
# stale (nym5-client2 and nym2-client1 rebuilt onto new IPs, nym2-client2's
# entry was actually a duplicate of nym2-client1's old IP), confirmed live
# 2026-07-15 while deploying the race-condition fix below: three of these
# four IPs no longer pointed at the right host.
NYM_VMS=(
    "204.168.204.120"   # nym5-client1
    "178.104.191.219"   # nym5-client2
    "95.216.218.124"    # nym2-client1
    "178.104.184.192"   # nym2-client2
)

for IP in "${NYM_VMS[@]}"; do
    echo "→ Updating $IP …"
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "root@${IP}" \
        EXIT_GW="$EXIT_GW" \
        'bash -s' << 'REMOTE'

# ── 1. nym-post-connect.sh (nft rules + routing fix only) ────────────────────
cat > /usr/local/bin/nym-post-connect.sh << 'SCRIPT'
#!/bin/bash
# BUG FIXED (2026-07-15, hit live 4 times in one night across all 4 nym VMs):
# the fixed `sleep 3` below assumed nym-vpnd's "inet nym" nftables table
# already existed by then. It doesn't reliably — the table only appears once
# the tunnel reaches a certain connection stage, which can take well over 3s
# (nym5's mixnet path especially, or any retry/rotation churn). When the
# table wasn't there yet, `nft ... inet nym ...` failed with "Could not
# process rule: No such file or directory" and the SSH-exemption rules
# silently never got added — so once the table DID appear moments later
# (with whatever restrictive default it has), SSH had no exemption and was
# fully blocked (not just mis-routed — a hard EPERM/reject), requiring
# manual console intervention every time. Now polls for the table (and then
# specifically the output chain's reject rule, since the table can exist as
# an empty container briefly before that rule appears) with a bounded retry
# instead of a fixed sleep.
TABLE_WAIT_MAX_S=90
TABLE_WAIT_INTERVAL_S=3

waited=0
while ! nft list table inet nym >/dev/null 2>&1; do
    if (( waited >= TABLE_WAIT_MAX_S )); then
        echo "nym-post-connect: 'inet nym' table did not appear after ${TABLE_WAIT_MAX_S}s — giving up on nft rules this run" >&2
        /usr/local/bin/nym-ssh-routing-fix.sh
        exit 1
    fi
    sleep "$TABLE_WAIT_INTERVAL_S"
    waited=$(( waited + TABLE_WAIT_INTERVAL_S ))
done

waited=0
REJECT_HANDLE=""
while [[ -z "$REJECT_HANDLE" ]]; do
    REJECT_HANDLE=$(nft -a list chain inet nym output 2>/dev/null \
      | grep "reject # handle" \
      | grep -v "tcp dport 53" | grep -v "udp dport 53" \
      | tail -1 | grep -o "handle [0-9]*" | awk "{print \$2}")
    [[ -n "$REJECT_HANDLE" ]] && break
    if (( waited >= TABLE_WAIT_MAX_S )); then
        echo "nym-post-connect: no reject rule found in 'inet nym output' after ${TABLE_WAIT_MAX_S}s — skipping output-chain exemptions" >&2
        break
    fi
    sleep "$TABLE_WAIT_INTERVAL_S"
    waited=$(( waited + TABLE_WAIT_INTERVAL_S ))
done

# Idempotent: this script is reasserted after every connect/reconnect/
# rotation (see nym-vpnd.service.d drop-in below) — without checking for an
# existing exemption first, each cycle would pile on duplicate rules
# forever.
if [[ -n "$REJECT_HANDLE" ]] && ! nft list chain inet nym output 2>/dev/null | grep -q "tcp sport 22 accept"; then
    nft insert rule inet nym output handle "$REJECT_HANDLE" tcp sport 22 accept
    nft insert rule inet nym output handle "$REJECT_HANDLE" ip daddr 10.0.0.0/16 accept
    nft insert rule inet nym output handle "$REJECT_HANDLE" oif "eth0" accept
fi
if ! nft list chain inet nym input 2>/dev/null | grep -q "tcp dport 22 accept"; then
    nft add rule inet nym input tcp dport 22 accept
fi
if ! nft list chain inet nym input 2>/dev/null | grep -q "ip saddr 10.0.0.0/16 accept"; then
    nft add rule inet nym input ip saddr 10.0.0.0/16 accept
fi
echo "Nym post-connect nft rules applied"
/usr/local/bin/nym-ssh-routing-fix.sh
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
