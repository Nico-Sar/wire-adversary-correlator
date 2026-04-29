#!/usr/bin/env bash
# scripts/nym_watchdog.sh
# =======================
# Deployed to /usr/local/bin/nym_watchdog.sh on all 4 Nym VMs and run as
# systemd service nym-watchdog.service.
#
# Every 30s:
#   1. If nym-vpnc reports anything other than "Connected": full recovery.
#   2. If connected in mix mode (nym5) but SOCKS5 is Disabled: re-enable it.
#      (Skipped for wg/nym2 — traffic routed at OS level, no proxy needed.)

LOG=/var/log/nym_watchdog.log
INTERVAL=30

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"
}

recover() {
    log "RECOVER: starting full recovery sequence"
    nym-vpnc disconnect || true
    sleep 8
    nym-vpnc socks5 disable || true
    sleep 1
    for i in 1 2 3 4 5; do
        if nym-vpnc socks5 enable --socks5-address 127.0.0.1:1080 --exit-random; then
            log "RECOVER: socks5 enable succeeded (attempt $i)"
            break
        fi
        log "RECOVER: socks5 enable attempt $i failed, retrying in 5s..."
        sleep 5
    done
    sleep 2
    if nym-vpnc connect --wait && /usr/local/bin/nym-post-connect.sh; then
        log "RECOVER: reconnect succeeded"
    else
        log "RECOVER: WARNING reconnect failed — will retry next cycle"
    fi
}

COLLECTION_LOCK=/tmp/nym_collection_active

log "watchdog started (interval=${INTERVAL}s)"

while true; do
    if [[ -f "$COLLECTION_LOCK" ]]; then
        log "collection active, skipping reconnect"
        sleep "$INTERVAL"
        continue
    fi

    STATUS=$(nym-vpnc status 2>/dev/null || echo "error")

    if echo "$STATUS" | grep -q "Connected"; then
        # Detect tunnel mode: "mix" = nym5 (mixnet), "wg" = nym2 (WireGuard)
        if echo "$STATUS" | grep -qi "mix"; then
            TUNNEL_MODE="mix"
        else
            TUNNEL_MODE="wg"
        fi

        if [[ "$TUNNEL_MODE" == "mix" ]]; then
            SOCKS5_STATUS=$(nym-vpnc socks5 status 2>/dev/null || echo "unknown")
            if echo "$SOCKS5_STATUS" | grep -qi "Disabled"; then
                log "SOCKS5: mix mode but SOCKS5 is Disabled — re-enabling"
                for i in 1 2 3 4 5; do
                    if nym-vpnc socks5 enable --socks5-address 127.0.0.1:1080 --exit-random; then
                        log "SOCKS5: re-enable succeeded (attempt $i)"
                        break
                    fi
                    log "SOCKS5: re-enable attempt $i failed, retrying in 5s..."
                    sleep 5
                done
            fi
        fi
    else
        log "STATUS: not connected (status=${STATUS//[$'\n']/ }) — triggering recovery"
        recover
    fi

    sleep "$INTERVAL"
done
