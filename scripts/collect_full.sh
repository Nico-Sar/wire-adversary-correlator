#!/usr/bin/env bash
# scripts/collect_full.sh
# ========================
# Full dataset collection with configurable visits per URL.
#
# URL sets:
#   vpn / tor : config/urls.txt                    (115 URLs)
#   nym5      : config/urls_nym5_extended.txt       (60 URLs)
#   nym2      : config/urls_nym2.txt                (100 URLs)
#
# Wall-time estimate per V visits/URL (all 4 modes run fully concurrently;
# wall time is the slowest mode, not the sum):
#   vpn/tor (2 clients):  115 × V × 6s  / 60 / 2
#   nym2    (2 clients):  100 × V × 34s / 60 / 2   ← bottleneck
#   V=4  →  ~2.3 h    V=8  →  ~4.5 h    V=16 →  ~9.1 h
#
# Parallelization (port-per-mode egress BPF isolates captures — no staging
# groups needed, since vpn=8080, tor=8081, nym5=8082, nym2=80 never collide):
#   All 4 modes, both clients each, simultaneous.
#
# Prerequisites:
#   bash scripts/setup_webserver_ports.sh   (run once to configure nginx)
#
# Usage (from repo root):
#   bash scripts/collect_full.sh              # default V=8
#   bash scripts/collect_full.sh 16           # V=16
#   bash scripts/collect_full.sh 8 data/run2  # V=8, custom output dir

set -euo pipefail

VISITS="${1:-8}"
OUTPUT="${2:-data/full}"
URLS="config/urls.txt"
URLS_NYM5="config/urls_nym5_extended.txt"
URLS_NYM2="config/urls_nym2.txt"

# ── Pre-flight ────────────────────────────────────────────────────────────────
for f in "$URLS" "$URLS_NYM5" "$URLS_NYM2"; do
    if [[ ! -f "$f" ]]; then
        echo "[error] $f not found. Run from repo root."
        exit 1
    fi
done

URLS_COUNT=$(grep -c "^[^#]" "$URLS")
NYM5_COUNT=$(grep -c "^[^#]" "$URLS_NYM5")
NYM2_COUNT=$(grep -c "^[^#]" "$URLS_NYM2")
if [[ "$URLS_COUNT" -ne 115 ]]; then
    echo "[error] $URLS: expected 115 URLs, got $URLS_COUNT"; exit 1
fi
if [[ "$NYM5_COUNT" -ne 60 ]]; then
    echo "[error] $URLS_NYM5: expected 60 URLs, got $NYM5_COUNT"; exit 1
fi
if [[ "$NYM2_COUNT" -ne 100 ]]; then
    echo "[error] $URLS_NYM2: expected 100 URLs, got $NYM2_COUNT"; exit 1
fi

# Rough wall-time estimate (minutes) — slowest mode, not the sum
VPN_WALL=$(( (115 * VISITS * 6  + 59) / 60 / 2 ))
NYM2_WALL=$(( (100 * VISITS * 34 + 59) / 60 / 2 ))
TOTAL_WALL=$VPN_WALL
(( NYM2_WALL > TOTAL_WALL )) && TOTAL_WALL=$NYM2_WALL

mkdir -p "$OUTPUT"

# ── Zero-byte pcap check ──────────────────────────────────────────────────────
# After each mode completes, warn if >10% of ingress pcaps are 0 bytes.
# Zero-byte ingress captures on nym2 almost always indicate the stale eth0
# default route bypassing the ingress router — fix with fix_nym2_routing.sh.
check_zero_byte_pcaps() {
    local mode_dir="$1"
    local mode_label="$2"
    [[ -d "$mode_dir" ]] || return 0
    local total zero_count pct
    total=$(find "$mode_dir" -name "*_ingress.pcap" 2>/dev/null | wc -l)
    [[ "$total" -gt 0 ]] || return 0
    zero_count=$(find "$mode_dir" -name "*_ingress.pcap" -size 0 2>/dev/null | wc -l)
    [[ "$zero_count" -gt 0 ]] || return 0
    pct=$(( zero_count * 100 / total ))
    if [[ "$pct" -gt 10 ]]; then
        echo ""
        echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        echo "  WARNING: $mode_label — $zero_count / $total ingress pcaps are 0 bytes ($pct%)"
        echo "  Likely cause: stale eth0 default route bypassing ingress router."
        echo "  Run:  bash scripts/fix_nym2_routing.sh"
        echo "  before continuing or re-running collection for this mode."
        echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        echo ""
    fi
}

echo "========================================"
echo " Full collection: V=$VISITS visits/URL"
echo " URLs: vpn/tor=115  nym5=60  nym2=100"
echo " Output: $OUTPUT"
echo " Estimated wall time: ~${TOTAL_WALL} min"
echo "========================================"

# ── All 4 modes, both clients each, fully concurrent ──────────────────────────
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

wait $PID_VPN1   || echo "[vpn-client1]   exited with error — continuing"
wait $PID_VPN2   || echo "[vpn-client2]   exited with error — continuing"
wait $PID_TOR1   || echo "[tor-client1]   exited with error — continuing"
wait $PID_TOR2   || echo "[tor-client2]   exited with error — continuing"
wait $PID_NYM5_1 || echo "[nym5-client1]  exited with error — continuing"
wait $PID_NYM5_2 || echo "[nym5-client2]  exited with error — continuing"
wait $PID_NYM2_1 || echo "[nym2-client1]  exited with error — continuing"
wait $PID_NYM2_2 || echo "[nym2-client2]  exited with error — continuing"
echo "[$(date +%H:%M:%S)] All modes done."

check_zero_byte_pcaps "$OUTPUT/vpn"  "vpn"
check_zero_byte_pcaps "$OUTPUT/tor"  "tor"
check_zero_byte_pcaps "$OUTPUT/nym5" "nym5"
check_zero_byte_pcaps "$OUTPUT/nym2" "nym2"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "========================================"
echo " Collection complete."
echo " JSONL logs:"
ls -lh "$OUTPUT"/*.jsonl 2>/dev/null || echo "  (none found — check for errors above)"
echo " Pcap counts:"
for mode in vpn tor nym5 nym2; do
    count=$(ls "$OUTPUT/$mode/"*.pcap 2>/dev/null | wc -l)
    echo "  $mode: $count pcaps"
done
echo "========================================"
