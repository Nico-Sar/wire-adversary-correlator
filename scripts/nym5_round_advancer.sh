#!/usr/bin/env bash
# scripts/nym5_round_advancer.sh
# ================================
# Per-client watchdog + auto-round-advancer for the nym5 fleet. Unlike
# coordinator_watchdog.sh (which watches all clients against one fixed
# round/stage and just restarts the same invocation), this tracks each
# client's OWN current stage independently and behaves three ways when a
# client's process isn't running:
#
#   1. Genuinely finished its per-client quota for the current stage (log
#      tail shows "[coordinator] done.") -> advance to the next stage file
#      and launch there. This is what nym5-client2 did manually on
#      2026-07-20 after exhausting its 1200/1200 round_06 allocation while
#      client1 (still mid-quota) kept going -- clients don't need to be in
#      lockstep, each just moves on when its own work is done.
#   2. Not running, log doesn't show "done." -> crashed/killed mid-run,
#      restart the SAME stage (resume logic in coordinator.py skips
#      already-collected visit_ids).
#   3. Running but stale (log untouched >STALE_S with ~0% cpu) -> kill and
#      restart the SAME stage (silent-hang safety net, see
#      coordinator_watchdog.sh for the original root cause).
#
# Usage:
#   nohup bash scripts/nym5_round_advancer.sh <client1>=<start_stage> [<client2>=<start_stage> ...] \
#       > /tmp/nym5_round_advancer.log 2>&1 &
# Example:
#   nohup bash scripts/nym5_round_advancer.sh nym5-client1=6 nym5-client2=7 nym5-client3=6 \
#       nym5-client4=6 nym5-client5=6 nym5-client6=6 > /tmp/nym5_round_advancer.log 2>&1 &
set -u
[[ $# -gt 0 ]] || { echo "usage: nym5_round_advancer.sh <client>=<start_stage> [...]" >&2; exit 1; }

CAMPAIGN_ROOT="data/campaign_nym5"
URLDIR="$CAMPAIGN_ROOT/_url_slices/light"
STALE_S=600
INTERVAL_S=120
# 2026-07-20: staggers every launch() call by STAGGER_S. Root-caused a
# fleet-wide freeze (all 6 clients stuck cycling hcloud resets, every one
# failing "SOCKS5 port 1080 not listening" on the SAME visit number) to a
# known Nym backend concurrency bug in ticketbook (bandwidth credential)
# retrieval when multiple devices on the same account request one at the
# same time -- see nymtech's own changelog ("Retrieve and update ticketbook
# in the same query" fix entry). Every prior restart cycle relaunched all
# 6 clients within the same ~1s window (confirmed live in this log: 6
# consecutive "launched" lines with identical timestamps), which reconnects
# all 6 devices on the shared account simultaneously every single time --
# a self-inflicted thundering herd against exactly the bug described above.
# Spacing launches out removes the simultaneity that triggers it.
STAGGER_S=25
# VISITS_LIGHT was originally decided as 48/client/URL for a 2-client nym5
# fleet (see docs/CAMPAIGN_RUNBOOK.md "Light-list visits/URL decision" --
# 48*2=96 visits/URL combined * 265 light URLs = 25,440 flows/mode, hitting
# the 25k target). With 6 clients now active, keeping 48/client would
# collect ~3x the target (76k flows) for no benefit, burning 3x the wall
# time per stage that could instead go toward finishing sooner. Rescaled to
# 16/client (16*6=96, same combined total/URL) so the fleet reaches the same
# target in roughly 1/3 the wall-clock time. (2026-07-20)
VISITS="${NYM5_VISITS_PER_URL:-16}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COORDINATOR_PYTHON="$REPO_ROOT/.venv/bin/python3"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [round-advancer] $*"; }

declare -A STAGE
for arg in "$@"; do
    client="${arg%%=*}"
    stage="${arg##*=}"
    STAGE["$client"]="$stage"
done

round_dir()  { printf "%s/round_%02d" "$CAMPAIGN_ROOT" "$1"; }
stage_file() { printf "%s/stage_%02d.txt" "$URLDIR" "$1"; }

launch() {
    local client="$1" stage="$2"
    local rd sf
    rd="$(round_dir "$stage")"
    sf="$(stage_file "$stage")"
    mkdir -p "$rd"
    cd "$REPO_ROOT"
    setsid nohup "$COORDINATOR_PYTHON" -m collector.coordinator --mode nym5 \
        --urls "$sf" --visits "$VISITS" \
        --output "$rd" --client "$client" --rotate-circuits --rotate-every 3 \
        > "$rd/log_${client}.txt" 2>&1 < /dev/null &
    disown
    log "$client launched on stage_$(printf %02d "$stage") -> $rd"
    sleep "$STAGGER_S"
}

log "started, tracking: $(for c in "${!STAGE[@]}"; do echo -n "$c@stage_$(printf %02d "${STAGE[$c]}") "; done)"

while true; do
    for client in "${!STAGE[@]}"; do
        stage="${STAGE[$client]}"
        rd="$(round_dir "$stage")"
        logfile="$rd/log_${client}.txt"
        pid=$(pgrep -f "collector.coordinator.*--client $client " | head -1)

        if [[ -n "$pid" ]]; then
            mtime=$(stat -c %Y "$logfile" 2>/dev/null || echo 0)
            now=$(date +%s)
            age=$(( now - mtime ))
            cpu=$(ps -p "$pid" -o %cpu= 2>/dev/null | tr -d ' ')
            if (( age > STALE_S )) && [[ "${cpu%.*}" -eq 0 ]] 2>/dev/null; then
                log "$client (pid $pid) stale ${age}s on stage_$(printf %02d "$stage") -- killing+restarting same stage"
                kill -9 "$pid" 2>/dev/null
                sleep 1
                launch "$client" "$stage"
            fi
            continue
        fi

        if [[ ! -f "$logfile" ]]; then
            log "$client has no log yet for stage_$(printf %02d "$stage") -- launching"
            launch "$client" "$stage"
            continue
        fi

        if tail -n 5 "$logfile" | grep -q '\[coordinator\] done\.'; then
            next=$(( stage + 1 ))
            next_sf="$(stage_file "$next")"
            if [[ -f "$next_sf" ]]; then
                log "$client finished stage_$(printf %02d "$stage") -- advancing to stage_$(printf %02d "$next")"
                STAGE[$client]=$next
                launch "$client" "$next"
            else
                log "$client finished stage_$(printf %02d "$stage") -- no next stage file ($next_sf) yet, idle"
            fi
        else
            log "$client not running, log doesn't show completion on stage_$(printf %02d "$stage") -- treating as crashed, restarting"
            launch "$client" "$stage"
        fi
    done
    sleep "$INTERVAL_S"
done
