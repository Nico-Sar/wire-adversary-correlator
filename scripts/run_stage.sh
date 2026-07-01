#!/usr/bin/env bash
# scripts/run_stage.sh
# =====================
# Runs ONE step of the nym campaign's stage grids: vpn against a full-list
# stage, tor against a tor-list stage (full-minus-zip — large zips stall over
# Tor bandwidth, producing junk flows), nym5/nym2 against a light-list stage.
# The full and tor grids share the same stage index but tor may be absent for
# rounds whose full-list stage was entirely .zip URLs (run_campaign.sh passes
# NONE for tor in that case). Either full/tor/light argument can be "NONE".
# All active modes launch concurrently (backgrounded, one wait, each client
# -> its own logfile, shared output dir).
#
# Locked parameters:
#   vpn:       --visits 25/client/URL
#   tor:       --visits 25/client/URL --rotate-circuits (from TOR_URLS env var
#              or falls back to FULL_URLS if TOR_URLS not set)
#   nym5/nym2: --rotate-circuits --rotate-every 3. Visits/client/URL is NOT
#              hardcoded — set VISITS_LIGHT explicitly (env var) before
#              calling this script. There is no safe default: 25 (matching
#              vpn/tor) yields only ~13.25k flows/mode on the 265-URL light
#              list, not the 25k target — see docs/CAMPAIGN_RUNBOOK.md
#              "Light-list visits/URL decision" for the arithmetic. This
#              script refuses to launch nym5/nym2 if VISITS_LIGHT is unset,
#              rather than silently guessing.
#
# Usage:
#   bash scripts/run_stage.sh <full_urls_file|NONE> <light_urls_file|NONE> <output_dir> [label]
#   TOR_URLS=<tor_stage_file|NONE> bash scripts/run_stage.sh ...  (set by run_campaign.sh)
#
# Exit code: 0 if all launched client processes completed (their own
# visit_status may still include errors/wedges — audit_stage.sh's job).
# Non-zero if the stage could not be launched at all.

set -uo pipefail   # no -e: one client's failure must not kill the others

FULL_URLS="${1:?usage: run_stage.sh <full_urls_file|NONE> <light_urls_file|NONE> <output_dir> [label]}"
LIGHT_URLS="${2:?usage: run_stage.sh <full_urls_file|NONE> <light_urls_file|NONE> <output_dir> [label]}"
OUTPUT="${3:?usage: run_stage.sh <full_urls_file|NONE> <light_urls_file|NONE> <output_dir> [label]}"
LABEL="${4:-stage}"

VISITS_FULL=25
VISITS_LIGHT="${VISITS_LIGHT:-}"   # no safe default — see header comment
# Tor uses a zip-filtered URL list (set by run_campaign.sh); falls back to
# FULL_URLS when called without this env var (backward compat, manual runs).
TOR_URLS="${TOR_URLS:-$FULL_URLS}"
ROTATE_EVERY_NYM=3
BACKFILL="${BACKFILL:-0}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/nico-thesis}"
SSH_OPTS="-i $SSH_KEY -o StrictHostKeyChecking=no -o ConnectTimeout=10 -o BatchMode=yes"
INGRESS_IP="204.168.184.30"
EGRESS_IP="204.168.189.97"
CLIENT_HANG_POLL_TIMEOUT_S=240
CLIENT_HANG_POLL_INTERVAL_S=10

# collector.coordinator needs the project venv (paramiko etc.) — system
# python3 has none of that and crashes instantly with ModuleNotFoundError.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COORDINATOR_PYTHON="${COORDINATOR_PYTHON:-$REPO_ROOT/.venv/bin/python3}"

log() { echo "[$(date '+%H:%M:%S')] [$LABEL] $*"; }
die() { echo "[$(date '+%H:%M:%S')] [$LABEL] [ERROR] $*" >&2; exit 1; }

[[ -x "$COORDINATOR_PYTHON" ]] \
    || die "venv python not found/executable: $COORDINATOR_PYTHON — collector.coordinator requires the project venv (paramiko, etc.), not system python3"

declare -A CLIENT_IP=(
    [vpn-client1]="204.168.205.5"     [vpn-client2]="204.168.184.39"
    [tor-client1]="89.167.102.181"    [tor-client2]="204.168.194.172"
    [nym5-client1]="204.168.204.120"  [nym5-client2]="204.168.201.84"
    [nym2-client1]="204.168.181.115"  [nym2-client2]="95.216.218.124"
)

# ── 0. Pre-flight ──────────────────────────────────────────────────────────────
RUN_FULL=0; RUN_TOR=0; RUN_LIGHT=0
if [[ "$FULL_URLS" != "NONE" ]]; then
    [[ -f "$FULL_URLS" ]] || die "full URLs file not found: $FULL_URLS"
    bad=$(grep -nE '^[[:space:]]*https?://' "$FULL_URLS" || true)
    [[ -z "$bad" ]] || die "$FULL_URLS contains full URLs, not bare paths:\n$bad"
    RUN_FULL=1
fi
if [[ "$TOR_URLS" != "NONE" ]]; then
    [[ -f "$TOR_URLS" ]] || die "tor URLs file not found: $TOR_URLS"
    bad=$(grep -nE '^[[:space:]]*https?://' "$TOR_URLS" || true)
    [[ -z "$bad" ]] || die "$TOR_URLS contains full URLs, not bare paths:\n$bad"
    RUN_TOR=1
fi
if [[ "$LIGHT_URLS" != "NONE" ]]; then
    [[ -f "$LIGHT_URLS" ]] || die "light URLs file not found: $LIGHT_URLS"
    bad=$(grep -nE '^[[:space:]]*https?://' "$LIGHT_URLS" || true)
    [[ -z "$bad" ]] || die "$LIGHT_URLS contains full URLs, not bare paths:\n$bad"
    RUN_LIGHT=1
    [[ -n "$VISITS_LIGHT" ]] || die "VISITS_LIGHT is not set and a light-list stage is active. Decide visits/URL for nym5/nym2 first — see docs/CAMPAIGN_RUNBOOK.md 'Light-list visits/URL decision'. Example: VISITS_LIGHT=48 bash scripts/run_stage.sh ..."
fi
(( RUN_FULL || RUN_TOR || RUN_LIGHT )) || die "FULL_URLS, TOR_URLS, and LIGHT_URLS are all NONE — nothing to run"

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
# the resilient layer on top). Detect, hcloud reset, poll, then launch that
# client. If it never comes back within budget, skip it for THIS stage only.
#
# Uses `reset` (hard power-cycle), not `reboot` (graceful ACPI signal) — a
# fully hung VM's kernel cannot process an ACPI signal at all, so `reboot`
# is a no-op against the actual failure mode this function exists for.
# collector.coordinator.recover_wedged_client's hard tier already uses
# `reset` for the same reason; confirmed live on the wire recovering a real
# powered-off nym2-client2 (see ALERTS.log trail in that fix).
ensure_client_reachable() {
    local client_id="$1" host="${CLIENT_IP[$1]}"
    if ssh $SSH_OPTS "root@$host" 'echo ok' >/dev/null 2>&1; then
        return 0
    fi
    log "WARNING: $client_id ($host) unreachable — attempting hcloud reset"
    local hcloud; hcloud=$(command -v hcloud || echo "$HOME/bin/hcloud")
    [[ -x "$hcloud" ]] || { log "ERROR: hcloud CLI not found — cannot recover $client_id"; return 1; }

    # Use the same per-server lock file as coordinator.py's _hcloud_reset() so
    # bash and Python callers never fire overlapping ops on the same VM.
    # Retry on "resource is locked" (another op in flight) with backoff.
    local lock_file="/tmp/hcloud_reset_${client_id}.lock"
    touch "$lock_file"
    local attempt=0 delay=15 reset_out reset_rc
    while true; do
        attempt=$(( attempt + 1 ))
        reset_out=$(flock -x "$lock_file" "$hcloud" server reset "$client_id" 2>&1)
        reset_rc=$?
        if [[ $reset_rc -eq 0 ]]; then
            break
        elif echo "$reset_out" | grep -qi "locked" && [[ $attempt -lt 8 ]]; then
            log "WARNING: $client_id hcloud reset locked (attempt $attempt/8) — retrying in ${delay}s"
            sleep "$delay"
            delay=$(( delay < 60 ? delay * 2 : 120 ))
        else
            log "ERROR: hcloud reset failed for $client_id (attempt $attempt): $reset_out"
            return 1
        fi
    done

    local deadline=$((SECONDS + CLIENT_HANG_POLL_TIMEOUT_S))
    while (( SECONDS < deadline )); do
        if ssh $SSH_OPTS "root@$host" 'echo ok' >/dev/null 2>&1; then
            log "$client_id back up after reset."
            return 0
        fi
        sleep "$CLIENT_HANG_POLL_INTERVAL_S"
    done
    log "ERROR: $client_id still unreachable after ${CLIENT_HANG_POLL_TIMEOUT_S}s — skipping for this stage"
    return 1
}

CANDIDATES=()
(( RUN_FULL ))  && CANDIDATES+=(vpn-client1 vpn-client2)
(( RUN_TOR ))   && CANDIDATES+=(tor-client1 tor-client2)
(( RUN_LIGHT )) && CANDIDATES+=(nym5-client1 nym5-client2 nym2-client1 nym2-client2)

REACHABLE=()
SKIPPED=()
for client_id in "${CANDIDATES[@]}"; do
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
    local client_id="$1" mode="$2" urls="$3" visits="$4"; shift 4
    "$COORDINATOR_PYTHON" -m collector.coordinator --mode "$mode" --urls "$urls" \
        --visits "$visits" --output "$OUTPUT" --client "$client_id" "$@" \
        > "$OUTPUT/log_${client_id}.txt" 2>&1 &
    PIDS[$client_id]=$!
}

is_reachable() { printf '%s\n' "${REACHABLE[@]}" | grep -qx "$1"; }

# ── 3b. Backfill setup (BACKFILL=1 only) ──────────────────────────────────────
# Compute URLs shared between the primary stage list and the light list so
# vpn/tor collect extra visits on those URLs while nym5 is still running.
# If there is no intersection (shouldn't happen in practice) fall back to the
# full stage URL list so backfill always has something to do.
BACKFILL_STOP="$OUTPUT/backfill_stop"
rm -f "$BACKFILL_STOP"
VPN_BF_ARGS=(); TOR_BF_ARGS=()
if (( BACKFILL && RUN_FULL && RUN_LIGHT )); then
    BACKFILL_VPN_URLS="$OUTPUT/_backfill_vpn_urls.txt"
    comm -12 <(sort "$FULL_URLS") <(sort "$LIGHT_URLS") > "$BACKFILL_VPN_URLS" 2>/dev/null || true
    if [[ ! -s "$BACKFILL_VPN_URLS" ]]; then
        cp "$FULL_URLS" "$BACKFILL_VPN_URLS"
        log "backfill vpn: no intersection with light list — falling back to full stage URLs"
    fi
    VPN_BF_ARGS=(--backfill-urls "$BACKFILL_VPN_URLS" --backfill-stop-file "$BACKFILL_STOP")
    log "backfill vpn: $(wc -l < "$BACKFILL_VPN_URLS" | tr -d ' ') shared URL(s) → $BACKFILL_VPN_URLS"
fi
if (( BACKFILL && RUN_TOR && RUN_LIGHT )); then
    BACKFILL_TOR_URLS="$OUTPUT/_backfill_tor_urls.txt"
    comm -12 <(sort "$TOR_URLS") <(sort "$LIGHT_URLS") > "$BACKFILL_TOR_URLS" 2>/dev/null || true
    if [[ ! -s "$BACKFILL_TOR_URLS" ]]; then
        cp "$TOR_URLS" "$BACKFILL_TOR_URLS"
        log "backfill tor: no intersection with light list — falling back to tor stage URLs"
    fi
    TOR_BF_ARGS=(--backfill-urls "$BACKFILL_TOR_URLS" --backfill-stop-file "$BACKFILL_STOP")
    log "backfill tor: $(wc -l < "$BACKFILL_TOR_URLS" | tr -d ' ') shared URL(s) → $BACKFILL_TOR_URLS"
fi

log "Launching stage: ${#REACHABLE[@]} clients (${REACHABLE[*]:-none}) -- vpn=$RUN_FULL tor=$RUN_TOR light=$RUN_LIGHT"
is_reachable vpn-client1  && (( RUN_FULL )) && launch_client vpn-client1  vpn  "$FULL_URLS" "$VISITS_FULL" "${VPN_BF_ARGS[@]}"
is_reachable vpn-client2  && (( RUN_FULL )) && launch_client vpn-client2  vpn  "$FULL_URLS" "$VISITS_FULL" "${VPN_BF_ARGS[@]}"
is_reachable tor-client1  && (( RUN_TOR ))  && launch_client tor-client1  tor  "$TOR_URLS"  "$VISITS_FULL" --rotate-circuits "${TOR_BF_ARGS[@]}"
is_reachable tor-client2  && (( RUN_TOR ))  && launch_client tor-client2  tor  "$TOR_URLS"  "$VISITS_FULL" --rotate-circuits "${TOR_BF_ARGS[@]}"
is_reachable nym5-client1 && launch_client nym5-client1 nym5 "$LIGHT_URLS" "$VISITS_LIGHT" --rotate-circuits --rotate-every "$ROTATE_EVERY_NYM"
is_reachable nym5-client2 && launch_client nym5-client2 nym5 "$LIGHT_URLS" "$VISITS_LIGHT" --rotate-circuits --rotate-every "$ROTATE_EVERY_NYM"
is_reachable nym2-client1 && launch_client nym2-client1 nym2 "$LIGHT_URLS" "$VISITS_LIGHT" --rotate-circuits --rotate-every "$ROTATE_EVERY_NYM"
is_reachable nym2-client2 && launch_client nym2-client2 nym2 "$LIGHT_URLS" "$VISITS_LIGHT" --rotate-circuits --rotate-every "$ROTATE_EVERY_NYM"

# ── 3c. Backfill stop-file monitor ────────────────────────────────────────────
# When BACKFILL=1 and nym5 clients were launched, watch their PIDs in a
# background subshell. Once every nym5 PID exits, touch the stop file so
# coordinator.py's backfill loop terminates. If no nym5 client launched,
# create the stop file immediately (nothing to wait for).
BACKFILL_MONITOR_PID=0
if (( BACKFILL && RUN_LIGHT )); then
    NYM5_PIDS=()
    for _c in nym5-client1 nym5-client2; do
        [[ -v PIDS[$_c] ]] && NYM5_PIDS+=("${PIDS[$_c]}")
    done
    if (( ${#NYM5_PIDS[@]} > 0 )); then
        (
            for _pid in "${NYM5_PIDS[@]}"; do
                while kill -0 "$_pid" 2>/dev/null; do sleep 30; done
            done
            touch "$BACKFILL_STOP"
        ) &
        BACKFILL_MONITOR_PID=$!
        log "backfill monitor started (watching nym5 PIDs: ${NYM5_PIDS[*]})"
    else
        log "backfill: no nym5 clients launched — creating stop file immediately"
        touch "$BACKFILL_STOP"
    fi
fi

launched_clients="${!PIDS[*]}"
{
    echo "full_urls=$FULL_URLS"
    echo "tor_urls=$TOR_URLS"
    echo "light_urls=$LIGHT_URLS"
    echo "output=$OUTPUT"
    echo "launched=${launched_clients:-none}"
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

echo "failed=${FAILED_CLIENTS[*]:-none}" >> "$OUTPUT/stage_meta.txt"

kill "$DROP_MONITOR_PID" 2>/dev/null || true
[[ "$BACKFILL_MONITOR_PID" -gt 0 ]] && kill "$BACKFILL_MONITOR_PID" 2>/dev/null || true

log "Stage done. Launched: ${#REACHABLE[@]}  Skipped: ${#SKIPPED[@]}  Process-failed: ${#FAILED_CLIENTS[@]}"
if [[ ${#SKIPPED[@]} -gt 0 || ${#FAILED_CLIENTS[@]} -gt 0 ]]; then
    log "*** Review before proceeding: skipped=${SKIPPED[*]:-none} process-failed=${FAILED_CLIENTS[*]:-none} ***"
fi

echo "$OUTPUT"
