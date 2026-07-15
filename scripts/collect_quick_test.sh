#!/usr/bin/env bash
# scripts/collect_quick_test.sh
# ==============================
# Quick end-to-end test: V=3 visits × small URL sets across all 4 modes.
# Validates the full pipeline (BPF filters, circuit rotation, port-per-mode
# egress isolation, resume logic) before committing to a full collection run.
#
# URL sets:
#   vpn / tor  : config/urls_quick_test.txt      (6 URLs)
#   nym5       : config/urls_quick_test_nym5.txt (4 URLs)
#   nym2       : config/urls_quick_test_nym2.txt (4 URLs, no pdf/mp3/mp4)
#
# Collection: all 4 modes, both clients each, fully concurrent — each mode
# has its own egress port (vpn=8080, tor=8081, nym5=8082, nym2=80), so there
# are no BPF capture collisions and no staging groups are needed.
#
# Per-mode visit counts (V=3):
#   vpn  : 6 × 3 = 18 visits × 2 clients = 36 total
#   tor  : 6 × 3 = 18 visits × 2 clients = 36 total
#   nym5 : 4 × 3 = 12 visits × 2 clients = 24 total
#   nym2 : 4 × 3 = 12 visits × 2 clients = 24 total
#
# Estimated wall time: ~25-30 minutes (nym5 is the bottleneck)
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
URLS_NYM2="config/urls_quick_test_nym2.txt"
VISITS=3
OUTPUT="data/quick_test"

for f in "$URLS" "$URLS_NYM5" "$URLS_NYM2"; do
    if [[ ! -f "$f" ]]; then
        echo "[error] $f not found. Run from repo root."
        exit 1
    fi
done

URLS_COUNT=$(grep -c "^[^#]" "$URLS")
NYM5_COUNT=$(grep -c "^[^#]" "$URLS_NYM5")
NYM2_COUNT=$(grep -c "^[^#]" "$URLS_NYM2")
if [[ "$URLS_COUNT" -ne 6 ]]; then
    echo "[error] $URLS: expected 6 URLs, got $URLS_COUNT"; exit 1
fi
if [[ "$NYM5_COUNT" -ne 4 ]]; then
    echo "[error] $URLS_NYM5: expected 4 URLs, got $NYM5_COUNT"; exit 1
fi
if [[ "$NYM2_COUNT" -ne 4 ]]; then
    echo "[error] $URLS_NYM2: expected 4 URLs, got $NYM2_COUNT"; exit 1
fi

mkdir -p "$OUTPUT"

echo "========================================"
echo " Quick test collection: V=$VISITS visits/URL"
echo " URLs: vpn/tor=6  nym5=4  nym2=4"
echo " Output: $OUTPUT"
echo " Estimated wall time: ~25-30 minutes"
echo "========================================"

echo ""
echo "[$(date +%H:%M:%S)] Concurrent start: vpn + tor + nym5 + nym2 (2 clients each) ..."

python3 -m collector.coordinator \
    --mode    vpn \
    --urls    "$URLS" \
    --visits  "$VISITS" \
    --output  "$OUTPUT" \
    --client  vpn-client1 &
PID_VPN1=$!

python3 -m collector.coordinator \
    --mode    vpn \
    --urls    "$URLS" \
    --visits  "$VISITS" \
    --output  "$OUTPUT" \
    --client  vpn-client2 &
PID_VPN2=$!

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

python3 -m collector.coordinator \
    --mode    nym2 \
    --urls    "$URLS_NYM2" \
    --visits  "$VISITS" \
    --output  "$OUTPUT" \
    --client  nym2-client1 \
    --rotate-circuits &
PID_NYM2_1=$!

python3 -m collector.coordinator \
    --mode    nym2 \
    --urls    "$URLS_NYM2" \
    --visits  "$VISITS" \
    --output  "$OUTPUT" \
    --client  nym2-client2 \
    --rotate-circuits &
PID_NYM2_2=$!

wait $PID_VPN1  || echo "[vpn-client1]  exited with error — continuing"
wait $PID_VPN2  || echo "[vpn-client2]  exited with error — continuing"
wait $PID_TOR1  || echo "[tor-client1]  exited with error — continuing"
wait $PID_TOR2  || echo "[tor-client2]  exited with error — continuing"
wait $PID_NYM5_1 || echo "[nym5-client1] exited with error — continuing"
wait $PID_NYM5_2 || echo "[nym5-client2] exited with error — continuing"
wait $PID_NYM2_1 || echo "[nym2-client1] exited with error — continuing"
wait $PID_NYM2_2 || echo "[nym2-client2] exited with error — continuing"
echo "[$(date +%H:%M:%S)] All modes done."

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "========================================"
echo " Quick test complete."
echo ""
echo " JSONL logs:"
for mode in vpn tor nym5 nym2; do
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
for mode in vpn tor nym5 nym2; do
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
