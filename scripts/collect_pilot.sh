#!/usr/bin/env bash
# scripts/collect_pilot.sh
# ========================
# Pilot collection: 5 URLs × 5 visits across all 8 clients / 5 modes.
#
# Topology:
#   baseline  → client1           (sequential)
#   vpn       → vpn-client1       (sequential)
#   tor       → tor-client1 + tor-client2          (parallel)
#   nym5      → nym5-client1 + nym5-client2         (parallel, rotate-circuits, urls_pilot_nym5.txt)
#   nym2      → nym2-client1 + nym2-client2         (parallel, rotate-circuits)
#
# Usage (from repo root):
#   bash scripts/collect_pilot.sh
#
# Output: data/pilot/

set -euo pipefail

URLS="config/urls_pilot.txt"
URLS_NYM5="config/urls_pilot_nym5.txt"
VISITS=5
OUTPUT="data/pilot"

for f in "$URLS" "$URLS_NYM5"; do
    if [[ ! -f "$f" ]]; then
        echo "[error] $f not found. Run from repo root."
        exit 1
    fi
done

echo "========================================"
echo " Pilot collection: 5 URLs × $VISITS visits"
echo " Output: $OUTPUT"
echo " Modes: baseline, vpn, tor, nym5, nym2"
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

# ── Tor (parallel) ───────────────────────────────────────────────────────────
echo ""
echo "[$(date +%H:%M:%S)] Starting tor (tor-client1 + tor-client2 in parallel)..."
python3 -m collector.coordinator \
    --mode    tor \
    --urls    "$URLS" \
    --visits  "$VISITS" \
    --output  "$OUTPUT" \
    --client  tor-client1 &
PID_TOR1=$!

python3 -m collector.coordinator \
    --mode    tor \
    --urls    "$URLS" \
    --visits  "$VISITS" \
    --output  "$OUTPUT" \
    --client  tor-client2 &
PID_TOR2=$!

wait $PID_TOR1 || echo "[tor-client1] exited with error — continuing"
wait $PID_TOR2 || echo "[tor-client2] exited with error — continuing"
echo "[$(date +%H:%M:%S)] tor done."

# ── Nym5 (parallel, rotate-circuits) ─────────────────────────────────────────
echo ""
echo "[$(date +%H:%M:%S)] Starting nym5 (nym5-client1 + nym5-client2 in parallel)..."
python3 -m collector.coordinator \
    --mode    nym5 \
    --urls    "$URLS_NYM5" \
    --visits  "$VISITS" \
    --output  "$OUTPUT" \
    --client  nym5-client1 \
    --rotate-circuits &
PID_NYM5_1=$!

python3 -m collector.coordinator \
    --mode    nym5 \
    --urls    "$URLS_NYM5" \
    --visits  "$VISITS" \
    --output  "$OUTPUT" \
    --client  nym5-client2 \
    --rotate-circuits &
PID_NYM5_2=$!

wait $PID_NYM5_1 || echo "[nym5-client1] exited with error — continuing"
wait $PID_NYM5_2 || echo "[nym5-client2] exited with error — continuing"
echo "[$(date +%H:%M:%S)] nym5 done."

# ── Nym2 (parallel, rotate-circuits) ─────────────────────────────────────────
echo ""
echo "[$(date +%H:%M:%S)] Starting nym2 (nym2-client1 + nym2-client2 in parallel)..."
python3 -m collector.coordinator \
    --mode    nym2 \
    --urls    "$URLS" \
    --visits  "$VISITS" \
    --output  "$OUTPUT" \
    --client  nym2-client1 \
    --rotate-circuits &
PID_NYM2_1=$!

python3 -m collector.coordinator \
    --mode    nym2 \
    --urls    "$URLS" \
    --visits  "$VISITS" \
    --output  "$OUTPUT" \
    --client  nym2-client2 \
    --rotate-circuits &
PID_NYM2_2=$!

wait $PID_NYM2_1 || echo "[nym2-client1] exited with error — continuing"
wait $PID_NYM2_2 || echo "[nym2-client2] exited with error — continuing"
echo "[$(date +%H:%M:%S)] nym2 done."

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "========================================"
echo " Collection complete."
echo " JSONL logs:"
ls -lh "$OUTPUT"/*.jsonl 2>/dev/null || echo "  (none found — check for errors above)"
echo " Pcap counts:"
for mode in baseline vpn tor nym5 nym2; do
    count=$(ls "$OUTPUT/$mode/"*.pcap 2>/dev/null | wc -l)
    echo "  $mode: $count pcaps"
done
echo "========================================"
