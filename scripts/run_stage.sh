#!/usr/bin/env bash
# scripts/run_stage.sh
# =====================
# Runs ONE stage of the nym campaign: all 4 modes, both clients each, fully
# concurrent (collect_quick_test.sh pattern — backgrounded, one wait, each
# client -> its own logfile, shared output dir).
#
# Locked parameters for this campaign:
#   --visits 25 (per client per URL; 2 clients -> 50 visits/URL/mode)
#   nym5/nym2: --rotate-circuits --rotate-every 3
#   vpn/tor:   --rotate-circuits (every visit — unaffected by this campaign)
#
# Usage:
#   bash scripts/run_stage.sh <stage_urls_file> <output_dir> [stage_label]
#
# Exit code: 0 if all 8 client processes completed (their own visit_status
# may still include errors/wedges — that's audit_stage.sh's job to assess).
# Non-zero if the stage could not be launched at all (agent missing, routers
# unreachable, or a client never came back after the hang-resilience retry).

set -uo pipefail   # no -e: one client's failure must not kill the others

STAGE_URLS="${1:?usage: run_stage.sh <stage_urls_file> <output_dir> [label]}"
OUTPUT="${2:?usage: run_stage.sh <stage_urls_file> <output_dir> [label]}"
LABEL="${3:-stage}"

VISITS=25
ROTATE_EVERY_NYM=3
SSH_KEY="${SSH_KEY:-$HOME/.ssh/nico-thesis}"
SSH_OPTS="-i $SSH_KEY -o StrictHostKeyChecking=no -o ConnectTimeout=10 -o BatchMode=yes"
INGRESS_IP="204.168.184.30"
EGRESS_IP="204.168.189.97"
CLIENT_HANG_POLL_TIMEOUT_S=240
CLIENT_HANG_POLL_INTERVAL_S=10

log() { echo "[$(date '+%H:%M:%S')] [$LABEL] $*"; }
die() { echo "[$(date '+%H:%M:%S')] [$LABEL] [ERROR] $*" >&2; exit 1; }

declare -A CLIENT_IP=(
    [vpn-client1]="204.168.205.5"     [vpn-client2]="204.168.184.39"
    [tor-client1]="89.167.102.181"    [tor-client2]="204.168.194.172"
    [nym5-client1]="204.168.204.120"  [nym5-client2]="204.168.201.84"
    [nym2-client1]="204.168.181.115"  [nym2-client2]="95.216.218.124"
)

# ── 0. Pre-flight ──────────────────────────────────────────────────────────────
[[ -f "$STAGE_URLS" ]] || die "stage URLs file not found: $STAGE_URLS"

# Doubled-URL guard — same check as validate_urls.sh, re-asserted here in case
# a slice file was hand-edited after stage 0.
bad_lines=$(grep -nE '^[[:space:]]*https?://' "$STAGE_URLS" || true)
[[ -z "$bad_lines" ]] || die "$STAGE_URLS contains full URLs, not bare paths:\n$bad_lines"

log "Checking ssh-agent has the campaign key loaded..."
ssh-add -l 2>/dev/null | grep -qi "nico-thesis\|nicolas-thesis" \
    || die "~/.ssh/nico-thesis not loaded in a running ssh-agent. Run: eval \"\$(ssh-agent -s)\" && ssh-add $SSH_KEY"

log "Checking routers reachable..."
ssh $SSH_OPTS "root@$INGRESS_IP" 'echo ok' >/dev/null 2>&1 || die "ingress router ($INGRESS_IP) unreachable"
ssh $SSH_OPTS "root@$EGRESS_IP"  'echo ok' >/dev/null 2>&1 || die "egress router ($EGRESS_IP) unreachable"
log "Routers OK."

mkdir -p "$OUTPUT"

# ── 1. Per-client hang resilience ──────────────────────────────────────────────
# A client unreachable at stage start used to crash the whole run with an
# uncaught RuntimeError (coordinator.py's initial connect is deliberately
# fail-fast — by design, for ad-hoc runs; the campaign launcher needs to be
# the resilient layer on top). Detect, hcloud reboot, poll, then launch that
# client. If it never comes back within budget, skip it for THIS stage only
# — the other 7 clients still launch; this mode just gets fewer visits this
# stage, which audit_stage.sh's yield check will surface.
ensure_client_reachable() {
    local client_id="$1" host="${CLIENT_IP[$1]}"
    if ssh $SSH_OPTS "root@$host" 'echo ok' >/dev/null 2>&1; then
        return 0
    fi
    log "WARNING: $client_id ($host) unreachable — attempting hcloud reboot"
    local hcloud; hcloud=$(command -v hcloud || echo "$HOME/bin/hcloud")
    [[ -x "$hcloud" ]] || { log "ERROR: hcloud CLI not found — cannot recover $client_id"; return 1; }
    if ! "$hcloud" server reboot "$client_id" >/dev/null 2>&1; then
        log "ERROR: hcloud reboot failed for $client_id"
        return 1
    fi
    local deadline=$((SECONDS + CLIENT_HANG_POLL_TIMEOUT_S))
    while (( SECONDS < deadline )); do
        if ssh $SSH_OPTS "root@$host" 'echo ok' >/dev/null 2>&1; then
            log "$client_id back up after reboot."
            return 0
        fi
        sleep "$CLIENT_HANG_POLL_INTERVAL_S"
    done
    log "ERROR: $client_id still unreachable after ${CLIENT_HANG_POLL_TIMEOUT_S}s — skipping for this stage"
    return 1
}

REACHABLE=()
SKIPPED=()
for client_id in vpn-client1 vpn-client2 tor-client1 tor-client2 \
                 nym5-client1 nym5-client2 nym2-client1 nym2-client2; do
    if ensure_client_reachable "$client_id"; then
        REACHABLE+=("$client_id")
    else
        SKIPPED+=("$client_id")
    fi
done

if [[ ${#SKIPPED[@]} -gt 0 ]]; then
    log "*** Skipped this stage (unreachable, could not recover): ${SKIPPED[*]} ***"
fi
[[ ${#REACHABLE[@]} -gt 0 ]] || die "no clients reachable — aborting stage entirely"

# ── 2. Periodic router drop sampling for the duration of this stage ───────────
( while true; do
    bash scripts/check_router_drops.sh snapshot /dev/stdout
    echo "---"
    sleep 600
  done ) >> "$OUTPUT/router_drops.log" 2>&1 &
DROP_MONITOR_PID=$!

# ── 3. Launch all reachable clients, concurrent, backgrounded ─────────────────
declare -A PIDS
launch_client() {
    local client_id="$1" mode="$2"; shift 2
    python3 -m collector.coordinator --mode "$mode" --urls "$STAGE_URLS" \
        --visits "$VISITS" --output "$OUTPUT" --client "$client_id" "$@" \
        > "$OUTPUT/log_${client_id}.txt" 2>&1 &
    PIDS[$client_id]=$!
}

is_reachable() { printf '%s\n' "${REACHABLE[@]}" | grep -qx "$1"; }

log "Launching stage: ${#REACHABLE[@]}/8 clients (${REACHABLE[*]})"
is_reachable vpn-client1  && launch_client vpn-client1  vpn
is_reachable vpn-client2  && launch_client vpn-client2  vpn
is_reachable tor-client1  && launch_client tor-client1  tor  --rotate-circuits
is_reachable tor-client2  && launch_client tor-client2  tor  --rotate-circuits
is_reachable nym5-client1 && launch_client nym5-client1 nym5 --rotate-circuits --rotate-every "$ROTATE_EVERY_NYM"
is_reachable nym5-client2 && launch_client nym5-client2 nym5 --rotate-circuits --rotate-every "$ROTATE_EVERY_NYM"
is_reachable nym2-client1 && launch_client nym2-client1 nym2 --rotate-circuits --rotate-every "$ROTATE_EVERY_NYM"
is_reachable nym2-client2 && launch_client nym2-client2 nym2 --rotate-circuits --rotate-every "$ROTATE_EVERY_NYM"

{
    echo "stage_urls=$STAGE_URLS"
    echo "output=$OUTPUT"
    echo "launched=${!PIDS[*]}"
    echo "skipped=${SKIPPED[*]:-none}"
    for c in "${!PIDS[@]}"; do echo "pid_$c=${PIDS[$c]}"; done
} > "$OUTPUT/stage_meta.txt"

log "Waiting for all launched clients to finish..."
FAILED_CLIENTS=()
for client_id in "${!PIDS[@]}"; do
    if ! wait "${PIDS[$client_id]}"; then
        log "WARNING: $client_id exited non-zero"
        FAILED_CLIENTS+=("$client_id")
    fi
done

kill "$DROP_MONITOR_PID" 2>/dev/null || true

log "Stage done. Launched: ${#REACHABLE[@]}  Skipped: ${#SKIPPED[@]}  Process-failed: ${#FAILED_CLIENTS[@]}"
if [[ ${#SKIPPED[@]} -gt 0 || ${#FAILED_CLIENTS[@]} -gt 0 ]]; then
    log "*** Review before proceeding: skipped=${SKIPPED[*]:-none} process-failed=${FAILED_CLIENTS[*]:-none} ***"
fi

echo "$OUTPUT"
