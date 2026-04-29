#!/usr/bin/env bash
# scripts/collect_pilot_nym.sh
# ============================
# Runs only nym5 and nym2 collection — assumes baseline, vpn, and tor are
# already done (collect_pilot.sh) and their JSONL logs exist in data/pilot/.
#
# Usage (from repo root):
#   bash scripts/collect_pilot_nym.sh
#
# Output appended to data/pilot/ (same directory as collect_pilot.sh).

set -euo pipefail

URLS="config/urls_pilot.txt"
URLS_NYM5="config/urls_pilot_nym5.txt"
VISITS=5
OUTPUT="data/pilot"

# ── Pre-flight checks ─────────────────────────────────────────────────────────
for f in "$URLS" "$URLS_NYM5"; do
    if [[ ! -f "$f" ]]; then
        echo "[error] $f not found. Run from repo root."
        exit 1
    fi
done

MISSING=()
for mode in baseline vpn tor; do
    jsonl="$OUTPUT/${mode}_visits.jsonl"
    [[ -f "$jsonl" ]] || MISSING+=("$jsonl")
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo "[error] Prior collection incomplete. Missing:"
    for f in "${MISSING[@]}"; do echo "  $f"; done
    echo "Run scripts/collect_pilot.sh first."
    exit 1
fi

echo "========================================"
echo " Nym-only pilot collection: 5 URLs × $VISITS visits"
echo " Output: $OUTPUT"
echo " Modes: nym5, nym2"
echo "========================================"

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
echo " Nym collection complete."
echo " JSONL logs:"
ls -lh "$OUTPUT"/*.jsonl 2>/dev/null || echo "  (none found — check for errors above)"
echo " Pcap counts:"
for mode in nym5 nym2; do
    count=$(ls "$OUTPUT/$mode/"*.pcap 2>/dev/null | wc -l)
    echo "  $mode: $count pcaps"
done
echo "========================================"
