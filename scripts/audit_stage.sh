#!/usr/bin/env bash
# scripts/audit_stage.sh
# ========================
# Runs after EVERY stage, before the next one is allowed to start. Computes
# 6 checks; HALTS (non-zero exit, no auto-proceed) on any red flag.
#
# Usage:
#   bash scripts/audit_stage.sh <stage_output_dir> <campaign_root> <license_deadline_YYYY-MM-DD>
#
# <campaign_root> must contain all stage output dirs so far (for cumulative
# flow counts in the budget tracker, item 6) — same parent dir run_campaign.sh
# passes to each run_stage.sh call.

set -uo pipefail

STAGE_DIR="${1:?usage: audit_stage.sh <stage_output_dir> <campaign_root> <license_deadline_YYYY-MM-DD>}"
CAMPAIGN_ROOT="${2:?usage: audit_stage.sh <stage_output_dir> <campaign_root> <license_deadline_YYYY-MM-DD>}"
LICENSE_DEADLINE="${3:?usage: audit_stage.sh <stage_output_dir> <campaign_root> <license_deadline_YYYY-MM-DD>}"

TARGET_FLOWS_PER_MODE=25000
YIELD_THRESHOLD=0.85

RED_FLAG=0
flag() { echo "  [FLAG] $*"; RED_FLAG=1; }
ok()   { echo "  [ok]   $*"; }

echo "================================================================"
echo " STAGE AUDIT — $STAGE_DIR"
echo " $(date '+%Y-%m-%d %H:%M:%S')"
echo "================================================================"

declare -A CLIENT_PRIVATE_IP=(
    [vpn-client1]="10.0.0.5"   [vpn-client2]="10.0.0.3"
    [tor-client1]="10.0.0.7"   [tor-client2]="10.0.0.8"
    [nym5-client1]="10.0.0.9"  [nym5-client2]="10.0.0.10"
    [nym2-client1]="10.0.0.4"  [nym2-client2]="10.0.0.6"
)

# ── 1. Per-mode yield ───────────────────────────────────────────────────────────
echo ""
echo "--- 1. Per-mode yield ---"
for mode in vpn tor nym5 nym2; do
    f="$STAGE_DIR/${mode}_visits.jsonl"
    [[ -f "$f" ]] || { echo "  $mode: no log found"; continue; }
    total=$(wc -l < "$f")
    success=$(grep -c '"visit_status": "success"' "$f" || true)
    [[ "$total" -gt 0 ]] || { echo "  $mode: 0 visits"; continue; }
    yield=$(awk -v s="$success" -v t="$total" 'BEGIN{printf "%.3f", s/t}')
    pct=$(awk -v y="$yield" 'BEGIN{printf "%.1f", y*100}')
    if awk -v y="$yield" -v th="$YIELD_THRESHOLD" 'BEGIN{exit !(y<th)}'; then
        flag "$mode: yield ${pct}% ($success/$total) — below ${YIELD_THRESHOLD}x threshold"
    else
        ok "$mode: yield ${pct}% ($success/$total)"
    fi
done

# ── 2. Success-flow validity (ingress_packets distribution) ────────────────────
echo ""
echo "--- 2. Success-flow validity (ingress_packets) ---"
for mode in vpn tor nym5 nym2; do
    f="$STAGE_DIR/${mode}_visits.jsonl"
    [[ -f "$f" ]] || continue
    python3 -c "
import json, sys
pkts = []
with open('$f') as fh:
    for line in fh:
        line = line.strip()
        if not line: continue
        try: r = json.loads(line)
        except Exception: continue
        if r.get('visit_status') == 'success':
            pkts.append(r.get('ingress_packets', 0))
if not pkts:
    print('  $mode: no successful visits to check')
    sys.exit(0)
pkts.sort()
n = len(pkts)
mn, md, mx = pkts[0], pkts[n//2], pkts[-1]
print(f'  $mode: n={n} min={mn} median={md} max={mx}' + ('  *** ZERO MIN ***' if mn == 0 else ''))
if mn == 0:
    sys.exit(1)
"
    [[ $? -ne 0 ]] && flag "$mode: at least one 'success' visit has ingress_packets=0 — zero-ingress guard should have caught this"
done
echo "  (nym2 legitimately thin ~100-200 pkts; nym5 thousands; vpn/tor in between — both fine, only zero is a problem)"

# ── 3. Router drops ─────────────────────────────────────────────────────────────
echo ""
echo "--- 3. Router drops (this stage's samples) ---"
DROPLOG="$STAGE_DIR/router_drops.log"
if [[ -f "$DROPLOG" ]]; then
    nonzero=$(grep -E "rx_dropped|rx_missed" "$DROPLOG" | awk '{print $2}' | grep -v '^0$' || true)
    if [[ -n "$nonzero" ]]; then
        flag "non-zero drops found in $DROPLOG:"
        grep -B1 -E "rx_dropped|rx_missed" "$DROPLOG" | grep -v '^0$\|--' | sed 's/^/    /'
    else
        n_samples=$(grep -c "rx_dropped" "$DROPLOG" || echo 0)
        ok "zero drops across $n_samples samples"
    fi
else
    flag "no router_drops.log found in $STAGE_DIR — drop monitor did not run"
fi

# ── 4. Contamination sweep ──────────────────────────────────────────────────────
echo ""
echo "--- 4. Contamination sweep (per-client ingress pcap scoping) ---"
contam_found=0
for mode in vpn tor nym5 nym2; do
    moddir="$STAGE_DIR/$mode"
    [[ -d "$moddir" ]] || continue
    for pcap in "$moddir"/*_ingress.pcap; do
        [[ -f "$pcap" ]] || continue
        base=$(basename "$pcap")
        client_id="${base%%_v*}"
        own_ip="${CLIENT_PRIVATE_IP[$client_id]:-}"
        [[ -n "$own_ip" ]] || continue
        foreign=$(tshark -r "$pcap" -T fields -e ip.src -e ip.dst 2>/dev/null \
            | tr '\t' '\n' | grep -E '^10\.0\.0\.' | sort -u | grep -v "^${own_ip}$" || true)
        if [[ -n "$foreign" ]]; then
            flag "$base: foreign 10.0.0.x address(es) present (expected only $own_ip): $foreign"
            contam_found=1
        fi
    done
done
[[ "$contam_found" -eq 0 ]] && ok "no foreign 10.0.0.x addresses in any ingress pcap"

# ── 5. Wedge accounting ─────────────────────────────────────────────────────────
echo ""
echo "--- 5. Wedge accounting ---"
declare -A WEDGE_TOTAL WEDGE_RECOVERED
wedge_clients_seen=0
for log in "$STAGE_DIR"/log_*.txt; do
    [[ -f "$log" ]] || continue
    client_id=$(basename "$log" .txt); client_id="${client_id#log_}"
    total=$(grep -c '\[wedge\]' "$log" || true)
    recovered=$(grep -c 'recovered via' "$log" || true)
    unrecoverable=$(grep -c 'giving up after' "$log" || true)
    [[ "$total" -gt 0 ]] || continue
    WEDGE_TOTAL[$client_id]=$total
    WEDGE_RECOVERED[$client_id]=$recovered
    wedge_clients_seen=$((wedge_clients_seen + 1))
    echo "  $client_id: $total wedge events, $recovered recovered, $unrecoverable unrecoverable"
    if [[ "$unrecoverable" -gt 0 ]]; then
        flag "$client_id: $unrecoverable WEDGE_UNRECOVERABLE event(s)"
    fi
done
if [[ "$wedge_clients_seen" -gt 0 ]]; then
    max_client="" max_n=0
    for c in "${!WEDGE_TOTAL[@]}"; do
        if [[ "${WEDGE_TOTAL[$c]}" -gt "$max_n" ]]; then max_n="${WEDGE_TOTAL[$c]}"; max_client="$c"; fi
    done
    other_sum=0; other_n=0
    for c in "${!WEDGE_TOTAL[@]}"; do
        [[ "$c" == "$max_client" ]] && continue
        other_sum=$((other_sum + WEDGE_TOTAL[$c])); other_n=$((other_n + 1))
    done
    if [[ "$other_n" -gt 0 ]]; then
        other_avg=$((other_sum / other_n))
        if [[ "$max_n" -gt $((other_avg * 2)) && "$max_n" -gt 3 ]]; then
            echo "  [note] $max_client has disproportionately more wedges ($max_n) than the average of others (~$other_avg) — known history: nym5-client1"
        fi
    fi
else
    ok "no wedge events this stage"
fi
if [[ -f "$STAGE_DIR/ALERTS.log" ]]; then
    flag "ALERTS.log present this stage — $(wc -l < "$STAGE_DIR/ALERTS.log") alert(s) fired:"
    sed 's/^/    /' "$STAGE_DIR/ALERTS.log"
fi

# ── 6. Budget tracker ────────────────────────────────────────────────────────────
echo ""
echo "--- 6. Budget tracker ---"
STAGE_META="$STAGE_DIR/stage_meta.txt"
python3 -c "
import json, glob, os, sys
from datetime import datetime, date

campaign_root = '$CAMPAIGN_ROOT'
stage_dir = '$STAGE_DIR'
deadline = date.fromisoformat('$LICENSE_DEADLINE')
today = date.today()
days_remaining = (deadline - today).days

target = $TARGET_FLOWS_PER_MODE
status_overall = 'ON TRACK'

for mode in ('vpn', 'tor', 'nym5', 'nym2'):
    # Cumulative successes across ALL stage dirs in campaign_root so far.
    cumulative = 0
    for f in sorted(glob.glob(os.path.join(campaign_root, '*', f'{mode}_visits.jsonl'))):
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line: continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get('visit_status') == 'success':
                    cumulative += 1

    # This stage's own rate: successes / stage wall-clock.
    this_stage_file = os.path.join(stage_dir, f'{mode}_visits.jsonl')
    this_success = 0
    timestamps = []
    if os.path.exists(this_stage_file):
        with open(this_stage_file) as fh:
            for line in fh:
                line = line.strip()
                if not line: continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get('visit_status') == 'success':
                    this_success += 1
                if 't_capture_start' in r:
                    timestamps.append(r['t_capture_start'])
                if 't_capture_end' in r:
                    timestamps.append(r['t_capture_end'])

    remaining = max(0, target - cumulative)
    if not timestamps or this_success == 0:
        print(f'  {mode}: cumulative={cumulative}/{target}  (no rate data this stage)')
        continue

    stage_wall_s = max(timestamps) - min(timestamps)
    stage_wall_h = stage_wall_s / 3600.0
    rate_per_hour = this_success / stage_wall_h if stage_wall_h > 0 else 0
    if rate_per_hour <= 0:
        print(f'  {mode}: cumulative={cumulative}/{target}  rate=0/hr -- cannot project')
        status_overall = 'OVER BUDGET — consider N=5 on nym5 or scope cut'
        continue

    hours_needed = remaining / rate_per_hour
    days_needed = hours_needed / 24.0
    margin = days_remaining - days_needed
    if margin < 0:
        mode_status = 'OVER BUDGET'
        status_overall = 'OVER BUDGET — consider N=5 on nym5 or scope cut'
    elif margin < days_remaining * 0.15:
        mode_status = 'TIGHT'
        if status_overall == 'ON TRACK':
            status_overall = 'TIGHT'
    else:
        mode_status = 'ON TRACK'

    print(f'  {mode}: cumulative={cumulative}/{target}  this-stage rate={rate_per_hour:.1f}/hr  '
          f'remaining-needed={days_needed:.2f}d  license-remaining={days_remaining}d  [{mode_status}]')

print()
print(f'OVERALL: {status_overall}')
sys.exit(1 if 'OVER BUDGET' in status_overall else 0)
"
budget_rc=$?
[[ "$budget_rc" -ne 0 ]] && flag "budget tracker reports OVER BUDGET — see above"

# ── Verdict ──────────────────────────────────────────────────────────────────────
echo ""
echo "================================================================"
if [[ "$RED_FLAG" -eq 1 ]]; then
    echo " VERDICT: HALT — red flag(s) above. Do not proceed to the next stage"
    echo " without review."
    echo "================================================================"
    exit 1
else
    echo " VERDICT: PASS — safe to proceed to the next stage."
    echo "================================================================"
    exit 0
fi
