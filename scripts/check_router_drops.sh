#!/usr/bin/env bash
# scripts/check_router_drops.sh
# ==============================
# Snapshots RX packet/drop/missed counters (and CPU load) on the ingress and
# egress routers' capture interfaces. tshark itself reports only
# "N packets captured" on exit — no drop stats (confirmed: tshark wraps the
# capture, it doesn't surface dumpcap-style pcap_stats on SIGTERM) — so kernel
# interface counters via `ip -s link` are the only reliable drop signal when
# multiple concurrent tshark captures share one interface.
#
# Usage:
#   bash scripts/check_router_drops.sh snapshot before.txt
#   ... run the concurrent collection window ...
#   bash scripts/check_router_drops.sh snapshot after.txt
#   bash scripts/check_router_drops.sh diff before.txt after.txt

set -euo pipefail

SSH_KEY="${SSH_KEY:-$HOME/.ssh/nico-thesis}"
SSH_OPTS="-i $SSH_KEY -o StrictHostKeyChecking=no -o ConnectTimeout=10"
INGRESS_IP="204.168.184.30"
EGRESS_IP="204.168.189.97"
INGRESS_IFACE="enp7s0"
EGRESS_IFACE="enp7s0"

snapshot() {
    local out="$1"
    {
        echo "timestamp $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "--- ingress ($INGRESS_IFACE) ---"
        ssh $SSH_OPTS "root@$INGRESS_IP" \
            "ip -s link show $INGRESS_IFACE | awk '/RX:/{getline; print \"rx_packets\", \$2; print \"rx_errors\", \$3; print \"rx_dropped\", \$4; print \"rx_missed\", \$5}'; echo cpu_load \$(cut -d' ' -f1-3 /proc/loadavg)"
        echo "--- egress ($EGRESS_IFACE) ---"
        ssh $SSH_OPTS "root@$EGRESS_IP" \
            "ip -s link show $EGRESS_IFACE | awk '/RX:/{getline; print \"rx_packets\", \$2; print \"rx_errors\", \$3; print \"rx_dropped\", \$4; print \"rx_missed\", \$5}'; echo cpu_load \$(cut -d' ' -f1-3 /proc/loadavg)"
    } | tee "$out"
}

diff_snapshots() {
    local before="$1" after="$2"
    echo "=== before ($before) ==="; cat "$before"
    echo ""
    echo "=== after ($after) ===";  cat "$after"
    echo ""
    echo "=== delta (rx_dropped, rx_missed should be ~0 for a clean run) ==="
    paste <(grep -E "rx_dropped|rx_missed" "$before") <(grep -E "rx_dropped|rx_missed" "$after") \
        | awk '{print $1, $2 " -> " $4, "(delta=" $4-$2 ")"}'
}

case "${1:-}" in
    snapshot) snapshot "${2:?usage: snapshot <outfile>}" ;;
    diff)     diff_snapshots "${2:?before file}" "${3:?after file}" ;;
    *) echo "Usage: $0 snapshot <outfile> | diff <before> <after>" >&2; exit 1 ;;
esac
