#!/usr/bin/env bash
# scripts/collect_full.sh
# ========================
# Full dataset collection with configurable visits per URL.
#
# URL sets:
#   baseline / vpn / tor : config/urls.txt               (115 URLs)
#   nym5                  : config/urls_nym5_extended.txt  (60 URLs)
#   nym2                  : config/urls_nym2.txt           (100 URLs)
#
# Wall-time estimate per V visits/URL:
#   Group 1 bottleneck (vpn): 115 × V × 6s / 60
#   Group 2 bottleneck (nym2): 100 × V × 34s / 60 / 2   (2 parallel clients)
#   Total ≈ V × (11.5 + 28.3) min ≈ V × 40 min
#   V=4  →  ~2.7 h    V=8  →  ~5.3 h    V=16 →  ~10.7 h
#
# Parallelization (port-per-mode egress BPF isolates captures):
#   Group 1 (simultaneous): baseline=port80  vpn=port8080  tor=port8081
#   Then sequential: nym5=port8082  (2 parallel clients)
#   Then sequential: nym2=port80    (2 parallel clients, safe after nym5 finishes)
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

# Rough wall-time estimate (minutes)
G1_WALL=$(( (115 * VISITS * 6 + 59) / 60 ))   # vpn is Group 1 bottleneck
G2_WALL=$(( (100 * VISITS * 34 + 59) / 60 / 2 ))  # nym2 / 2 clients
TOTAL_WALL=$(( G1_WALL + G2_WALL ))

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
echo " URLs: baseline/vpn/tor=115  nym5=60  nym2=100"
echo " Output: $OUTPUT"
echo " Estimated wall time: ~${TOTAL_WALL} min"
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
check_zero_byte_pcaps "$OUTPUT/baseline" "baseline"
check_zero_byte_pcaps "$OUTPUT/vpn"      "vpn"
check_zero_byte_pcaps "$OUTPUT/tor"      "tor"

# ── Nym5 (parallel clients, after Group 1) ────────────────────────────────────
# Egress BPF port 8082 — no conflict with nym2 (port 80, runs after).
echo ""
echo "[$(date +%H:%M:%S)] nym5 start (nym5-client1 + nym5-client2 in parallel)..."

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
check_zero_byte_pcaps "$OUTPUT/nym5" "nym5"

# ── Nym2 (parallel clients, after nym5) ──────────────────────────────────────
# Egress BPF port 80 — safe because nym5 (port 8082) has finished.
echo ""
echo "[$(date +%H:%M:%S)] nym2 start (nym2-client1 + nym2-client2 in parallel)..."

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

wait $PID_NYM2_1 || echo "[nym2-client1] exited with error — continuing"
wait $PID_NYM2_2 || echo "[nym2-client2] exited with error — continuing"
echo "[$(date +%H:%M:%S)] nym2 done."
check_zero_byte_pcaps "$OUTPUT/nym2" "nym2"

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
