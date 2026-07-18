#!/usr/bin/env bash
# scripts/nym_watchdog.sh
# =======================
# Deployed to /usr/local/bin/nym_watchdog.sh on all 4 Nym VMs and run as
# systemd service nym-watchdog.service.
#
# Every 30s:
#   1. If nym-vpnc reports anything other than "Connected", OR is connected
#      in the WRONG tunnel mode for this VM: full recovery.
#   2. If connected in mix mode (nym5) but SOCKS5 is Disabled: re-enable it.
#      (Skipped for wg/nym2 — traffic routed at OS level, no proxy needed.)
#
# PROPOSED (2026-07-12, NOT YET APPLIED — see patches/09_nym_watchdog_mode_aware.md):
# this script previously had ZERO mode awareness -- recover() always ran the
# same disconnect/socks5/connect sequence regardless of which VM it was on,
# and NEVER asserted `nym-vpnc tunnel set --two-hop off`, anywhere. Confirmed
# live (2026-07-12): nym5-client2 repeatedly came back from recover() in
# "wg" (WireGuard) mode instead of "mix" (mixnet) -- silently wrong, and
# never self-corrected, because the main loop below only re-checks health
# when nym-vpnc reports NOT connected; "Connected wg" on a nym5 VM was
# treated as a healthy steady-state forever. Fixed by reading this VM's
# INTENDED mode from /etc/nym-watchdog-mode (written by an updated
# scripts/deploy_nym_watchdog.sh -- see that script's patch note) and: (a)
# asserting --two-hop off before connecting on mix-mode VMs, (b) treating
# "connected but in the wrong mode" the same as "not connected" so a VM
# that somehow drifts back into the wrong mode self-corrects on the next
# 30s cycle instead of being accepted indefinitely.
#
# SAFE-DEFAULT RULE: if /etc/nym-watchdog-mode is missing or unrecognized,
# INTENDED_MODE stays empty and NONE of the new mode-specific behavior
# applies -- this VM behaves EXACTLY as the pre-patch script did (the
# socks5 block still runs unconditionally, matching old behavior; no
# two-hop assertion, matching old behavior; no wrong-mode detection, since
# we don't know what "wrong" means for this VM). This is deliberate: with 4
# VMs sharing one script, a VM whose mode file didn't get deployed correctly
# must fail safe into old behavior, never guess.

LOG=/var/log/nym_watchdog.log
INTERVAL=30

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"
}

# ── Intended mode (PROPOSED) ────────────────────────────────────────────────
INTENDED_MODE_FILE=/etc/nym-watchdog-mode
INTENDED_MODE=""
if [[ -f "$INTENDED_MODE_FILE" ]]; then
    INTENDED_MODE=$(tr -d '[:space:]' < "$INTENDED_MODE_FILE")
fi
if [[ "$INTENDED_MODE" != "mix" && "$INTENDED_MODE" != "wg" ]]; then
    if [[ -n "$INTENDED_MODE" ]]; then
        log "WARNING: $INTENDED_MODE_FILE contains unrecognized value '${INTENDED_MODE}' -- ignoring, falling back to mode-unaware (pre-patch) behavior"
    else
        log "WARNING: $INTENDED_MODE_FILE missing -- falling back to mode-unaware (pre-patch) behavior. Re-run scripts/deploy_nym_watchdog.sh to fix."
    fi
    INTENDED_MODE=""
else
    log "intended mode for this VM: $INTENDED_MODE"
fi

recover() {
    log "RECOVER: starting full recovery sequence (intended mode: ${INTENDED_MODE:-unknown})"
    nym-vpnc disconnect || true
    sleep 8

    # PROPOSED: socks5 block now skipped only for confirmed wg-mode VMs
    # (nym2 -- doesn't use it at all, per the header comment). For mix
    # (nym5) AND for unknown/unmigrated VMs it still runs unconditionally,
    # exactly matching pre-patch behavior for the unknown case.
    if [[ "$INTENDED_MODE" != "wg" ]]; then
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
    fi

    # PROPOSED: this is the actual bug fix. Only runs when INTENDED_MODE is
    # confirmed "mix" -- never guessed for an unknown VM, never applied to a
    # confirmed wg VM. Verified via `nym-vpnc tunnel get` (confirmed live,
    # 2026-07-12: reports "Two-hop: off/on" directly, independent of
    # connection state -- a more direct check than only inferring mode from
    # `status` after connecting, and catches a failed assertion BEFORE
    # wasting a connect attempt on it) rather than just trusting `tunnel
    # set`'s own exit code, which may report success without the setting
    # actually having taken. Retried up to 3x: a failed/silently-ignored
    # assertion here, with no retry and no verification, is exactly how
    # nym5-client2 kept coming back in the wrong mode.
    if [[ "$INTENDED_MODE" == "mix" ]]; then
        TWO_HOP_OK=false
        for i in 1 2 3; do
            nym-vpnc tunnel set --two-hop off || true
            if nym-vpnc tunnel get 2>/dev/null | grep -qi "Two-hop: off"; then
                log "RECOVER: two-hop off confirmed via 'tunnel get' (attempt $i)"
                TWO_HOP_OK=true
                break
            fi
            log "RECOVER: two-hop off not yet confirmed (attempt $i), retrying in 3s..."
            sleep 3
        done
        if [[ "$TWO_HOP_OK" != true ]]; then
            log "RECOVER: *** WARNING *** could not confirm two-hop off after 3 attempts -- proceeding to connect anyway, post-connect status check below will flag if this VM comes back in the wrong mode"
        fi
    fi

    if nym-vpnc connect --wait && /usr/local/bin/nym-post-connect.sh; then
        log "RECOVER: reconnect succeeded"
        # PROPOSED: verify the mode actually took, don't just trust the
        # assertion above succeeded silently.
        if [[ "$INTENDED_MODE" == "mix" ]]; then
            POST_STATUS=$(nym-vpnc status 2>/dev/null || echo "error")
            if echo "$POST_STATUS" | grep -qi "mix"; then
                log "RECOVER: confirmed mix mode after reconnect"
            else
                log "RECOVER: *** WARNING *** intended mode is mix but post-connect status does NOT show mix mode (status=${POST_STATUS//[$'\n']/ }) -- two-hop assertion did not take effect"
            fi
        fi
    else
        log "RECOVER: WARNING reconnect failed — will retry next cycle"
    fi
}

COLLECTION_LOCK=/tmp/nym_collection_active

# Startup grace period: nym-watchdog and nym-vpnd both auto-start at boot
# (systemd WantedBy=multi-user.target). Without this delay, watchdog's first
# status check fires immediately, sees "not Connected" (nym-vpnd has only
# just started), and races its own recover() against safe-start's disconnect
# hook and any other boot-time automation touching the same nftables/ip-rule
# state -- confirmed live (2026-07-06) to break SSH. nym5-client1 never hit
# this in practice because it's never actually rebooted; any VM that does
# get rebooted needs this grace period so the daemon's own startup settles
# before watchdog ever looks at it.
STARTUP_GRACE_S=60
log "watchdog started (interval=${INTERVAL}s), startup grace period ${STARTUP_GRACE_S}s"
sleep "$STARTUP_GRACE_S"

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

        # PROPOSED: "Connected" alone used to be treated as fully healthy
        # regardless of TUNNEL_MODE -- a nym5 VM connected in "wg" mode was
        # accepted forever, since nothing here ever re-checked it once
        # nym-vpnc stopped reporting "not Connected". Now: connected in the
        # wrong mode for this VM's INTENDED_MODE is treated the same as not
        # connected at all, so it self-corrects on the next cycle instead of
        # staying silently wrong indefinitely. No-op when INTENDED_MODE is
        # unknown (unmigrated VM) -- same safe-default rule as recover().
        WRONG_MODE=false
        if [[ "$INTENDED_MODE" == "mix" && "$TUNNEL_MODE" != "mix" ]]; then
            WRONG_MODE=true
        elif [[ "$INTENDED_MODE" == "wg" && "$TUNNEL_MODE" == "mix" ]]; then
            WRONG_MODE=true
        fi

        if [[ "$WRONG_MODE" == true ]]; then
            log "STATUS: connected but in WRONG mode (intended=$INTENDED_MODE, actual=$TUNNEL_MODE) — triggering recovery"
            recover
        elif [[ "$TUNNEL_MODE" == "mix" ]]; then
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
