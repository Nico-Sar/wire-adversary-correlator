#!/usr/bin/env bash
# scripts/coordinator_watchdog.sh
# ================================
# Detects a hung coordinator.py process (log file stale for STALE_S with
# ~0% CPU -- the silent-hang pattern root-caused 2026-07-20 in
# check_client_health()'s raw exec_command()+recv_exit_status() call,
# fixed in coordinator.py but kept here as a safety net for any other
# unprotected blocking call that might still exist) and restarts it with
# the same invocation. Only targets nym5-client* (where the hang was
# observed); tor/vpn clients are left alone.
#
# Usage: nohup bash scripts/coordinator_watchdog.sh <round_out_dir> <client1> [client2 ...] > /tmp/coordinator_watchdog.log 2>&1 &
set -u
ROUND_OUT="${1:?usage: coordinator_watchdog.sh <round_out_dir> <client1> [client2 ...]}"
shift
CLIENTS_TO_WATCH=("$@")
[[ ${#CLIENTS_TO_WATCH[@]} -gt 0 ]] || { echo "usage: coordinator_watchdog.sh <round_out_dir> <client1> [client2 ...]" >&2; exit 1; }
STALE_S=600
INTERVAL_S=120
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COORDINATOR_PYTHON="$REPO_ROOT/.venv/bin/python3"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [watchdog] $*"; }

log "started, watching $ROUND_OUT for [${CLIENTS_TO_WATCH[*]}], stale threshold ${STALE_S}s"

while true; do
    # 2026-07-20: explicit client list, not a glob over log_nym5-client*.txt
    # -- the glob picked up nym5-client2's log (a client that already
    # finished its full round_06 allocation and was deliberately not
    # running) and kept relaunching it forever. Only watch clients this
    # invocation was actually told to manage.
    for client in "${CLIENTS_TO_WATCH[@]}"; do
        logfile="$ROUND_OUT/log_${client}.txt"
        [[ -f "$logfile" ]] || continue
        pid=$(pgrep -f "collector.coordinator.*--client $client " | head -1)

        restart=0
        if [[ -z "$pid" ]]; then
            # 2026-07-20: process has fully exited (e.g. crashed on the
            # initial fail-fast retry_ssh_connect() during a bad-luck
            # connectivity window -- confirmed live, client4/5/6 all died
            # this way on restart). Previously this case was silently
            # skipped (only stale-but-alive was handled), so a crashed
            # client just stayed dead forever with nothing watching it.
            log "$client has no running process -- treating as crashed, restarting"
            restart=1
        else
            mtime=$(stat -c %Y "$logfile" 2>/dev/null || echo 0)
            now=$(date +%s)
            age=$(( now - mtime ))
            cpu=$(ps -p "$pid" -o %cpu= 2>/dev/null | tr -d ' ')
            if (( age > STALE_S )) && [[ "${cpu%.*}" -eq 0 ]] 2>/dev/null; then
                log "$client (pid $pid) stale ${age}s, cpu=${cpu}% -- restarting"
                kill -9 "$pid" 2>/dev/null
                sleep 1
                restart=1
            fi
        fi

        if (( restart )); then
            cd "$REPO_ROOT"
            setsid nohup "$COORDINATOR_PYTHON" -m collector.coordinator --mode nym5 \
                --urls data/campaign_nym5/_url_slices/light/stage_06.txt --visits 48 \
                --output "$ROUND_OUT" --client "$client" --rotate-circuits --rotate-every 3 \
                > "$logfile" 2>&1 < /dev/null &
            disown
            log "$client restarted"
        fi
    done
    sleep "$INTERVAL_S"
done
