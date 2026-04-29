#!/usr/bin/env bash
# scripts/collect_quick_test.sh
# ==============================
# Quick end-to-end test: V=3 visits × small URL sets across all 5 modes.
# Validates the full pipeline (BPF filters, circuit rotation, port-per-mode
# egress isolation, resume logic) before committing to a full collection run.
#
# URL sets:
#   baseline / vpn / tor / nym2 : config/urls_quick_test.txt      (6 URLs)
#   nym5                         : config/urls_quick_test_nym5.txt (4 URLs)
#
# Collection order (port-per-mode egress isolation):
#   Group 1 (simultaneous): baseline=port80  vpn=port8080  tor=port8081
#   Then: nym5=port8082  (2 clients parallel)
#   Then: nym2=port80    (2 clients parallel, safe after Group 1 finishes)
#
# Per-mode visit counts (V=3):
#   baseline : 6 × 3 = 18 visits
#   vpn      : 6 × 3 = 18 visits
#   tor      : 6 × 3 = 18 visits × 2 clients = 36 total
#   nym5     : 4 × 3 = 12 visits × 2 clients = 24 total
#   nym2     : 6 × 3 = 18 visits × 2 clients = 36 total
#
# Estimated wall time: ~45-60 minutes
#   Group 1 : ~5 min  (tor is bottleneck at 18 × ~8s / 60)
#   nym5    : ~25 min (4 URLs × 3 × ~43s × rotation overhead / 2 clients)
#   nym2    : ~20 min (6 URLs × 3 × ~34s / 2 clients)
#
# Prerequisites:
#   bash scripts/setup_webserver_ports.sh   (run once to configure nginx)
#
# Usage (from repo root):
#   bash scripts/collect_quick_test.sh
#
# Output: data/quick_test/

set -euo pipefail

URLS="config/urls_quick_test.txt"
URLS_NYM5="config/urls_quick_test_nym5.txt"
VISITS=3
OUTPUT="data/quick_test"

for f in "$URLS" "$URLS_NYM5"; do
    if [[ ! -f "$f" ]]; then
        echo "[error] $f not found. Run from repo root."
        exit 1
    fi
done

URLS_COUNT=$(grep -c "^[^#]" "$URLS")
NYM5_COUNT=$(grep -c "^[^#]" "$URLS_NYM5")
if [[ "$URLS_COUNT" -ne 6 ]]; then
    echo "[error] $URLS: expected 6 URLs, got $URLS_COUNT"; exit 1
fi
if [[ "$NYM5_COUNT" -ne 4 ]]; then
    echo "[error] $URLS_NYM5: expected 4 URLs, got $NYM5_COUNT"; exit 1
fi

mkdir -p "$OUTPUT"

echo "========================================"
echo " Quick test collection: V=$VISITS visits/URL"
echo " URLs: baseline/vpn/tor/nym2=6  nym5=4"
echo " Output: $OUTPUT"
echo " Estimated wall time: ~45-60 minutes"
echo "========================================"

# ── Group 1: baseline + vpn + tor (simultaneous) ─────────────────────────────
echo ""
echo "[$(date +%H:%M:%S)] Group 1 start: baseline + vpn + tor running simultaneously..."

python3 -m collector.coordinator \
    --mode    baseline \
    --urls    "$URLS" \
    --visits  "$VISITS" \
    --output  "$OUTPUT" \
    --client  client1 &
PID_BASELINE=$!

python3 -m collector.coordinator \
    --mode    vpn \
    --urls    "$URLS" \
    --visits  "$VISITS" \
    --output  "$OUTPUT" \
    --client  vpn-client1 &
PID_VPN=$!

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

wait $PID_BASELINE || echo "[baseline]    exited with error — continuing"
wait $PID_VPN      || echo "[vpn]         exited with error — continuing"
wait $PID_TOR1     || echo "[tor-client1] exited with error — continuing"
wait $PID_TOR2     || echo "[tor-client2] exited with error — continuing"
echo "[$(date +%H:%M:%S)] Group 1 done."

# ── Nym5 (parallel clients) ───────────────────────────────────────────────────
echo ""
echo "[$(date +%H:%M:%S)] nym5 start (nym5-client1 + nym5-client2 in parallel, 4 URLs × $VISITS visits each)..."

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

# ── Nym2 (parallel clients, after nym5) ──────────────────────────────────────
# Egress port 80 — safe because Group 1 (baseline also port 80) has finished.
echo ""
echo "[$(date +%H:%M:%S)] nym2 start (nym2-client1 + nym2-client2 in parallel, 6 URLs × $VISITS visits each)..."

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
echo " Quick test complete."
echo ""
echo " JSONL logs:"
for mode in baseline vpn tor nym5 nym2; do
    log="$OUTPUT/${mode}_visits.jsonl"
    if [[ -f "$log" ]]; then
        total=$(grep -c . "$log" 2>/dev/null || echo 0)
        success=$(grep -c '"visit_status": "success"' "$log" 2>/dev/null || echo 0)
        echo "  $mode: $success/$total successful visits"
    else
        echo "  $mode: no log found"
    fi
done
echo ""
echo " Pcap sizes:"
for mode in baseline vpn tor nym5 nym2; do
    dir="$OUTPUT/$mode"
    if [[ -d "$dir" ]]; then
        count=$(ls "$dir"/*.pcap 2>/dev/null | wc -l)
        size=$(du -sh "$dir" 2>/dev/null | cut -f1)
        echo "  $mode: $count pcaps  ($size)"
    else
        echo "  $mode: no pcaps"
    fi
done
echo "========================================"
