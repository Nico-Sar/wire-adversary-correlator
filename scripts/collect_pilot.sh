#!/usr/bin/env bash
# scripts/collect_pilot.sh
# ========================
# Runs pilot collection: 5 URLs × 5 visits for baseline, tor, and vpn.
# Nym is excluded until mentor access is confirmed.
#
# Usage (from repo root):
#   bash scripts/collect_pilot.sh
#
# Output layout:
#   data/pilot/
#     baseline/     ← ingress + egress pcaps
#     tor/
#     vpn/
#     baseline_visits.jsonl
#     tor_visits.jsonl
#     vpn_visits.jsonl
#
# Wall time: ~10 min per mode (5 URLs × 5 visits × ~25s/visit avg)
# Total: ~30 min. Tor is the slowest; run baseline first to validate infra.
 
set -euo pipefail
 
URLS="config/urls_pilot.txt"
VISITS=5
OUTPUT="data/pilot"
 
# Abort if the URL file doesn't exist
if [[ ! -f "$URLS" ]]; then
    echo "[error] $URLS not found. Run from repo root."
    exit 1
fi
 
echo "========================================"
echo " Pilot collection: 5 URLs × $VISITS visits"
echo " Output: $OUTPUT"
echo " Modes: baseline, tor, vpn"
echo "========================================"
 
# ── Baseline ─────────────────────────────────────────────────────────────────
echo ""
echo "[$(date +%H:%M:%S)] Starting baseline..."
python3 -m collector.coordinator \
    --mode    baseline \
    --urls    "$URLS" \
    --visits  "$VISITS" \
    --output  "$OUTPUT" \
    --client  client1
 
echo "[$(date +%H:%M:%S)] baseline done."
 
# ── Tor ───────────────────────────────────────────────────────────────────────
echo ""
echo "[$(date +%H:%M:%S)] Starting tor..."
python3 -m collector.coordinator \
    --mode    tor \
    --urls    "$URLS" \
    --visits  "$VISITS" \
    --output  "$OUTPUT" \
    --client  tor-client1
 
echo "[$(date +%H:%M:%S)] tor done."
 
# ── VPN ───────────────────────────────────────────────────────────────────────
echo ""
echo "[$(date +%H:%M:%S)] Starting vpn..."
python3 -m collector.coordinator \
    --mode    vpn \
    --urls    "$URLS" \
    --visits  "$VISITS" \
    --output  "$OUTPUT" \
    --client  vpn-client1
 
echo "[$(date +%H:%M:%S)] vpn done."
 
echo ""
echo "========================================"
echo " Collection complete."
echo " JSONL logs:"
ls -lh "$OUTPUT"/*.jsonl 2>/dev/null || echo "  (none found — check for errors above)"
echo " Pcap counts:"
for mode in baseline tor vpn; do
    count=$(ls "$OUTPUT/$mode/"*.pcap 2>/dev/null | wc -l)
    echo "  $mode: $count pcaps (expected $(( VISITS * 5 * 2 )))"
done
echo "========================================"