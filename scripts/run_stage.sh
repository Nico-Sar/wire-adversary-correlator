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
#
# Instance separation (2026-07-12, see patches/10_nym5_instance_separation_design.md):
# MODE_SCOPE=both|nym5|fast (env var, default "both" = exact previous
# behavior). Needed because the light URL grid drives BOTH nym5 and nym2
# clients — run_campaign.sh forcing full/tor stage args to "NONE" is
# enough to keep vpn/tor out of a nym5-only instance, but nym5 and nym2
# can't be told apart by grid alone, only by this flag. "nym5" launches
# only nym5-client1/2 when light is active; "fast" launches only
# nym2-client1/2 when light is active (alongside vpn/tor). Also scopes the
# stage_meta filename — see the comment at that write site.

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

# PROPOSED (2026-07-12, applying patches/10_nym5_instance_separation_design.md):
# the light URL grid drives BOTH nym5 and nym2 clients — RUN_LIGHT alone
# can't tell them apart. MODE_SCOPE splits them explicitly: "nym5" launches
# only nym5-client1/2 when light is active (never nym2); "fast" launches
# only nym2-client1/2 when light is active (never nym5), alongside
# vpn/tor. Default "both" reproduces the exact previous behavior (all
# clients whose grid is active get launched) — matches run_campaign.sh's
# own default and keeps direct/manual invocations of this script unchanged.
MODE_SCOPE="${MODE_SCOPE:-both}"
case "$MODE_SCOPE" in
    both|nym5|fast) ;;
    *) die "invalid MODE_SCOPE='$MODE_SCOPE' (expected both|nym5|fast)" ;;
esac

[[ -x "$COORDINATOR_PYTHON" ]] \
    || die "venv python not found/executable: $COORDINATOR_PYTHON — collector.coordinator requires the project venv (paramiko, etc.), not system python3"

# Sourced live from config/infrastructure.py (not hardcoded) — a hardcoded
# copy here silently drifts every time a client VM is rebuilt with a new IP
# (confirmed: this map was stale for 3 of 4 nym VMs after the 2026-07-06/07
# rebuilds, which would have made ensure_client_reachable() below probe dead
# IPs and skip healthy VMs from every round).
declare -A CLIENT_IP
while IFS='=' read -r k v; do CLIENT_IP["$k"]="$v"; done < <(
    "$COORDINATOR_PYTHON" -c "
import sys; sys.path.insert(0, '$REPO_ROOT')
from config.infrastructure import CLIENTS
for name, cfg in CLIENTS.items():
    print(f'{name}=' + cfg['host'])
"
)

# Group membership sourced live from config/infrastructure.py, same reason
# as CLIENT_IP above (2026-07-20: hardcoded nym5-client1/2-only candidate
# lists silently excluded nym5-client3/4 after they were added to
# CLIENT_GROUPS — config and this script's candidate lists must not drift).
declare -A CLIENT_GROUP
while IFS='=' read -r k v; do CLIENT_GROUP["$k"]="$v"; done < <(
    "$COORDINATOR_PYTHON" -c "
import sys; sys.path.insert(0, '$REPO_ROOT')
from config.infrastructure import CLIENT_GROUPS
for mode, clients in CLIENT_GROUPS.items():
    print(f'{mode}=' + ' '.join(clients))
"
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
if [[ "$MODE_SCOPE" != "nym5" ]]; then
    (( RUN_FULL ))  && CANDIDATES+=(${CLIENT_GROUP[vpn]})
    (( RUN_TOR ))   && CANDIDATES+=(${CLIENT_GROUP[tor]})
fi
if [[ "$MODE_SCOPE" != "fast" ]]; then
    (( RUN_LIGHT )) && CANDIDATES+=(${CLIENT_GROUP[nym5]})
fi
if [[ "$MODE_SCOPE" != "nym5" ]]; then
    (( RUN_LIGHT )) && CANDIDATES+=(${CLIENT_GROUP[nym2]})
fi

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

# REMOVED (2026-07-12, applying patches/10_nym5_instance_separation_design.md):
# the backfill subsystem (§3b setup, §3c stop-file monitor, and the
# --backfill-urls/--backfill-stop-file args below) existed only to keep
# vpn/tor/nym2 busy while trapped waiting on nym5 within one shared round.
# Now that nym5 runs as its own independent instance (MODE_SCOPE), no mode
# ever waits on another to close a round, so there's nothing left to fill
# time with — deleted rather than adapted. coordinator.py's own
# --backfill-urls/--backfill-stop-file handling is untouched (out of scope
# for this pass) but is now simply never invoked from here again.

log "Launching stage: ${#REACHABLE[@]} clients (${REACHABLE[*]:-none}) -- vpn=$RUN_FULL tor=$RUN_TOR light=$RUN_LIGHT"
for c in ${CLIENT_GROUP[vpn]:-}; do
    is_reachable "$c" && (( RUN_FULL )) && launch_client "$c" vpn "$FULL_URLS" "$VISITS_FULL"
done
for c in ${CLIENT_GROUP[tor]:-}; do
    is_reachable "$c" && (( RUN_TOR )) && launch_client "$c" tor "$TOR_URLS" "$VISITS_FULL" --rotate-circuits
done
for c in ${CLIENT_GROUP[nym5]:-}; do
    is_reachable "$c" && launch_client "$c" nym5 "$LIGHT_URLS" "$VISITS_LIGHT" --rotate-circuits --rotate-every "$ROTATE_EVERY_NYM"
done
for c in ${CLIENT_GROUP[nym2]:-}; do
    is_reachable "$c" && launch_client "$c" nym2 "$LIGHT_URLS" "$VISITS_LIGHT" --rotate-circuits --rotate-every "$ROTATE_EVERY_NYM"
done

# REMOVED (2026-07-12): §3c backfill stop-file monitor — see the note at
# the launch step above for why the whole subsystem is gone, not adapted.

# PROPOSED (2026-07-12, applying patches/10_nym5_instance_separation_design.md):
# stage_meta.txt is now named by MODE_SCOPE (the actual instance identity),
# not re-derived from RUN_FULL/RUN_TOR/RUN_LIGHT — those three no longer
# map cleanly onto "which instance" once nym2 travels with the fast
# instance despite sharing RUN_LIGHT with nym5 (e.g. a "fast" round where
# vpn/tor have exhausted but nym2 is still going would have RUN_FULL=0,
# RUN_TOR=0, RUN_LIGHT=1 — indistinguishable from a real nym5-only round
# by grid flags alone). This matters for exactly one situation: the
# one-time round_03 transitional window where both new instances briefly
# write into the same existing round_03 directory — with a single fixed
# filename, whichever invocation finished last would silently clobber the
# other's launch record. Every future round lives in a separate
# campaign_root per instance, where only one of these filenames will ever
# be written, so this has no effect there beyond the (harmless) renaming.
# audit_stage.sh reads all stage_meta*.txt files present and merges them.
case "$MODE_SCOPE" in
    nym5) META_FILE="$OUTPUT/stage_meta_light.txt" ;;
    fast) META_FILE="$OUTPUT/stage_meta_full.txt" ;;
    both) META_FILE="$OUTPUT/stage_meta.txt" ;;   # old-style combined invocation, unchanged name
esac

launched_clients="${!PIDS[*]}"
{
    echo "full_urls=$FULL_URLS"
    echo "tor_urls=$TOR_URLS"
    echo "light_urls=$LIGHT_URLS"
    echo "output=$OUTPUT"
    echo "launched=${launched_clients:-none}"
    echo "skipped=${SKIPPED[*]:-none}"
    for c in "${!PIDS[@]}"; do echo "pid_$c=${PIDS[$c]}"; done
} > "$META_FILE"

log "Waiting for all launched clients to finish..."
FAILED_CLIENTS=()
for client_id in "${!PIDS[@]}"; do
    if ! wait "${PIDS[$client_id]}"; then
        log "WARNING: $client_id exited non-zero"
        FAILED_CLIENTS+=("$client_id")
    fi
done

echo "failed=${FAILED_CLIENTS[*]:-none}" >> "$META_FILE"

kill "$DROP_MONITOR_PID" 2>/dev/null || true

log "Stage done. Launched: ${#REACHABLE[@]}  Skipped: ${#SKIPPED[@]}  Process-failed: ${#FAILED_CLIENTS[@]}"
if [[ ${#SKIPPED[@]} -gt 0 || ${#FAILED_CLIENTS[@]} -gt 0 ]]; then
    log "*** Review before proceeding: skipped=${SKIPPED[*]:-none} process-failed=${FAILED_CLIENTS[*]:-none} ***"
fi

echo "$OUTPUT"
