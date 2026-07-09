#!/usr/bin/env bash
# scripts/pipeline_check.sh
# =========================
# Pre-collection health check for all 4 modes.
# Run this on leroy before staged_collect.sh. Takes ~2 minutes.
#
# What it checks:
#   1. SSH reachability  — all 11 VMs
#   2. tshark            — ingress + egress routers
#   3. Web server        — ports 80, 8080, 8081, 8082
#   4. Clock sync        — chronyc on both routers (drift threshold 5 ms)
#   5. Tor               — daemon up, NEWNYM responds (tor-client1, tor-client2)
#   6. WireGuard         — wg0 interface up (vpn-client1, vpn-client2)
#   7. nym-vpnc status   — all 4 Nym clients; flags any non-Connected state
#   8. nym2 routing      — no stale eth0 default route, tun1 interface present
#   9. nym5 SOCKS5       — port 1080 listening on nym5 clients
#
# Flags:
#   --fix-nym        Attempt nym-vpnc reconnect on any disconnected Nym client
#   --quick-test     If all critical checks pass, run collect_quick_test.sh (~60 min)
#
# Nym license renewal (do this BEFORE running --fix-nym if license is expired):
#   ssh -i ~/.ssh/nico-thesis root@<nym-client-ip>
#   nym-vpnc account status          # shows expiry / credential state
#   nym-vpnc account login --mnemonic "<your 12-word mnemonic>"   # re-authenticate
#   nym-vpnc connect --wait && /usr/local/bin/nym-post-connect.sh
#
# Usage (from repo root on leroy):
#   bash scripts/pipeline_check.sh
#   bash scripts/pipeline_check.sh --fix-nym
#   bash scripts/pipeline_check.sh --fix-nym --quick-test
# ─────────────────────────────────────────────────────────────────────────────

set -uo pipefail   # no -e: we want to keep checking even if individual checks fail

SSH_KEY="${SSH_KEY:-$HOME/.ssh/nico-thesis}"
SSH_OPTS="-i $SSH_KEY -o StrictHostKeyChecking=no -o ConnectTimeout=10 -o BatchMode=yes"
SSH="ssh $SSH_OPTS"
CLOCK_DRIFT_THRESHOLD_MS=5

FIX_NYM=false
RUN_QUICK_TEST=false
for arg in "$@"; do
    case "$arg" in
        --fix-nym)      FIX_NYM=true ;;
        --quick-test)   RUN_QUICK_TEST=true ;;
        *) echo "Unknown flag: $arg" >&2; exit 1 ;;
    esac
done

# ── VM inventory (sourced live from config/infrastructure.py) ────────────────
# A hardcoded copy here previously drifted stale after every client VM
# rebuild (confirmed for 3 of 4 nym VMs after the 2026-07-06/07 rebuilds).
INGRESS_IP="204.168.184.30"
EGRESS_IP="204.168.189.97"
WEB_SERVER_IP="204.168.163.45"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
declare -A CLIENT_IP
while IFS='=' read -r k v; do CLIENT_IP["$k"]="$v"; done < <(
    python3 -c "
import sys; sys.path.insert(0, '$REPO_ROOT')
from config.infrastructure import CLIENTS
for name, cfg in CLIENTS.items():
    print(f'{name}=' + cfg['host'])
"
)

# ── Result tracking ───────────────────────────────────────────────────────────
PASS=0
FAIL=0
WARN=0

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "  [${GREEN}PASS${NC}] $*"; (( PASS++ )) || true; }
fail() { echo -e "  [${RED}FAIL${NC}] $*"; (( FAIL++ )) || true; }
warn() { echo -e "  [${YELLOW}WARN${NC}] $*"; (( WARN++ )) || true; }
section() { echo ""; echo "── $* ──────────────────────────────────────────────────────"; }

vm_ssh() {
    # vm_ssh <ip> <command>
    $SSH root@"$1" "$2" 2>/dev/null
}

vm_ssh_ok() {
    # Returns 0 if command succeeds, 1 if it fails
    $SSH root@"$1" "$2" >/dev/null 2>&1
}

# ── 1. SSH reachability ───────────────────────────────────────────────────────
section "1. SSH reachability"

for label in ingress egress webserver; do
    case $label in
        ingress)   ip=$INGRESS_IP ;;
        egress)    ip=$EGRESS_IP ;;
        webserver) ip=$WEB_SERVER_IP ;;
    esac
    if vm_ssh_ok "$ip" "true"; then
        pass "$label ($ip)"
    else
        fail "$label ($ip) — cannot SSH"
    fi
done

for client in vpn-client1 vpn-client2 tor-client1 tor-client2 \
              nym5-client1 nym5-client2 nym2-client1 nym2-client2; do
    ip="${CLIENT_IP[$client]}"
    if vm_ssh_ok "$ip" "true"; then
        pass "$client ($ip)"
    else
        fail "$client ($ip) — cannot SSH"
    fi
done

# ── 2. tshark on routers ─────────────────────────────────────────────────────
section "2. tshark on routers"

for label in ingress egress; do
    ip=$( [[ $label == ingress ]] && echo "$INGRESS_IP" || echo "$EGRESS_IP" )
    ver=$(vm_ssh "$ip" "tshark --version 2>/dev/null | head -1" 2>/dev/null)
    if [[ -n "$ver" ]]; then
        pass "tshark on $label: $ver"
    else
        fail "tshark on $label: not found or version check failed"
    fi
done

# ── 3. Web server ports ───────────────────────────────────────────────────────
section "3. Web server (checked from egress router, 10.1.x subnet)"

declare -A PORT_MODE=(
    [80]="nym2"
    [8080]="vpn"
    [8081]="tor"
    [8082]="nym5"
)

WEB_PRIVATE_IP="10.1.0.3"
for port in 80 8080 8081 8082; do
    code=$(vm_ssh "$EGRESS_IP" \
        "curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 \
         http://${WEB_PRIVATE_IP}:${port}/page_html_1.html 2>/dev/null || echo 000")
    label="${PORT_MODE[$port]}"
    if [[ "$code" == "200" ]]; then
        pass "port $port ($label): HTTP 200"
    elif [[ "$code" == "000" ]]; then
        fail "port $port ($label): no response (nginx down or port not configured?)"
    else
        fail "port $port ($label): HTTP $code"
    fi
done

# ── 4. Clock sync ─────────────────────────────────────────────────────────────
section "4. Clock sync (chronyc)"

get_chrony_offset_ms() {
    local ip="$1"
    vm_ssh "$ip" "LC_ALL=C chronyc tracking 2>/dev/null" \
        | LC_NUMERIC=C awk '/System time/ {gsub(/[+-]/,"",$4); printf "%.3f", $4 * 1000}'
}

in_ms=$(get_chrony_offset_ms "$INGRESS_IP")
eg_ms=$(get_chrony_offset_ms "$EGRESS_IP")

if [[ -n "$in_ms" ]]; then
    pass "ingress chrony offset: ${in_ms} ms"
else
    fail "ingress chrony: could not read offset"
    in_ms=9999
fi

if [[ -n "$eg_ms" ]]; then
    pass "egress  chrony offset: ${eg_ms} ms"
else
    fail "egress chrony: could not read offset"
    eg_ms=9999
fi

if [[ -n "$in_ms" && -n "$eg_ms" ]]; then
    delta=$(LC_NUMERIC=C awk "BEGIN {d=${in_ms}-${eg_ms}; if(d<0) d=-d; printf \"%.3f\",d}")
    ok=$(LC_NUMERIC=C awk "BEGIN {print (${delta} <= ${CLOCK_DRIFT_THRESHOLD_MS}) ? \"yes\" : \"no\"}")
    if [[ "$ok" == "yes" ]]; then
        pass "inter-router delta: ${delta} ms (threshold: ${CLOCK_DRIFT_THRESHOLD_MS} ms)"
    else
        fail "inter-router delta: ${delta} ms EXCEEDS ${CLOCK_DRIFT_THRESHOLD_MS} ms — timing correlation will be wrong"
    fi
fi

# ── 5. Tor daemon ─────────────────────────────────────────────────────────────
section "5. Tor (tor-client1, tor-client2)"

for client in tor-client1 tor-client2; do
    ip="${CLIENT_IP[$client]}"
    # Check port 9050
    listening=$(vm_ssh "$ip" "ss -tnlp | grep ':9050' || true")
    if [[ -n "$listening" ]]; then
        pass "$client: Tor SOCKS5 port 9050 listening"
    else
        fail "$client: Tor port 9050 not listening (Tor daemon down?)"
    fi
    # Quick NEWNYM smoke-test
    newnym=$(vm_ssh "$ip" \
        "(printf 'AUTHENTICATE \"thesis2026\"\r\nSIGNAL NEWNYM\r\n'; sleep 1) | nc -q 1 127.0.0.1 9051 2>/dev/null || echo failed")
    if [[ "$newnym" == *"250 OK"* ]]; then
        pass "$client: NEWNYM accepted"
    else
        warn "$client: NEWNYM response unclear — $newnym"
    fi
done

# ── 6. WireGuard (vpn-client1, vpn-client2) ───────────────────────────────────
section "6. WireGuard (vpn-client1, vpn-client2)"

for client in vpn-client1 vpn-client2; do
    ip="${CLIENT_IP[$client]}"
    wg_status=$(vm_ssh "$ip" "wg show wg0 2>/dev/null | head -5 || echo 'not found'")
    if [[ "$wg_status" == *"not found"* ]]; then
        fail "$client: wg0 not found — WireGuard not running"
    elif [[ -z "$wg_status" ]]; then
        fail "$client: wg show returned nothing"
    else
        pass "$client: wg0 up"
        echo "    $(echo "$wg_status" | head -2 | tr '\n' ' ')"
    fi
done

# ── 7. Nym client status ──────────────────────────────────────────────────────
section "7. nym-vpnc status + license"

NYM_ALL_OK=true

check_nym_client() {
    local client="$1"
    local ip="${CLIENT_IP[$client]}"

    # Raw nym-vpnc status
    local status
    status=$(vm_ssh "$ip" "nym-vpnc status 2>/dev/null || echo 'ERROR: nym-vpnc not found'")

    if [[ "$status" == *"Connected"* ]]; then
        pass "$client: nym-vpnc Connected"
        # Extract gateway info
        local gw
        gw=$(echo "$status" | grep -oP '\[.*?\]' | head -2 | tr '\n' ' ' || true)
        [[ -n "$gw" ]] && echo "    gateways: $gw"
    elif [[ "$status" == *"ERROR: nym-vpnc not found"* ]]; then
        fail "$client: nym-vpnc binary not found on $ip"
        NYM_ALL_OK=false
    elif [[ "$status" == *"Disconnected"* || "$status" == *"disconnected"* ]]; then
        fail "$client: nym-vpnc Disconnected"
        NYM_ALL_OK=false
        # Show account status to detect license expiry
        local acct
        acct=$(vm_ssh "$ip" "nym-vpnc account status 2>&1 || true" | head -5)
        echo "    account status: $acct"
        if [[ "$acct" == *"expire"* || "$acct" == *"invalid"* || "$acct" == *"credential"* ]]; then
            warn "$client: possible license/credential issue — see renewal instructions in header"
        fi
        if $FIX_NYM; then
            echo "    [fix] attempting nym-vpnc connect..."
            local fix_out
            fix_out=$(vm_ssh "$ip" \
                "nym-vpnc connect --wait && /usr/local/bin/nym-post-connect.sh 2>&1 | tail -3" \
                || echo "reconnect failed")
            echo "    $fix_out"
            # Re-check
            local recheck
            recheck=$(vm_ssh "$ip" "nym-vpnc status 2>/dev/null || echo unknown")
            if [[ "$recheck" == *"Connected"* ]]; then
                pass "$client: reconnect SUCCEEDED"
                NYM_ALL_OK=true   # may still be false for other clients
            else
                fail "$client: reconnect FAILED — manual intervention needed"
            fi
        else
            echo "    → run with --fix-nym to attempt automatic reconnect"
            echo "    → if license expired: ssh root@$ip then: nym-vpnc account login --mnemonic \"<phrase>\""
        fi
    else
        warn "$client: unexpected status — $status"
        NYM_ALL_OK=false
    fi
}

for client in nym5-client1 nym5-client2 nym2-client1 nym2-client2; do
    check_nym_client "$client"
done

# ── 8. nym2-specific routing ──────────────────────────────────────────────────
section "8. nym2 routing (tun1 + no stale eth0 default route)"

for client in nym2-client1 nym2-client2; do
    ip="${CLIENT_IP[$client]}"

    # tun1 must exist and have an IP
    tun1=$(vm_ssh "$ip" "ip addr show tun1 2>/dev/null | grep 'inet ' | awk '{print \$2}'" || true)
    if [[ -n "$tun1" ]]; then
        pass "$client: tun1 up ($tun1)"
    else
        fail "$client: tun1 interface not found — nym2 WireGuard tunnel is down"
    fi

    # No stale eth0 default route (known issue: stale route causes 0-byte egress pcaps)
    stale=$(vm_ssh "$ip" "ip route show | grep 'default.*eth0' || true")
    if [[ -z "$stale" ]]; then
        pass "$client: no stale eth0 default route"
    else
        fail "$client: stale eth0 default route present: $stale"
        echo "    → fix: ssh root@$ip && ip route del default via 172.31.1.1 dev eth0"
        echo "    → or run:  bash scripts/fix_nym2_routing.sh"
    fi
done

# ── 9. nym5 SOCKS5 ───────────────────────────────────────────────────────────
section "9. nym5 SOCKS5 proxy (port 1080)"

for client in nym5-client1 nym5-client2; do
    ip="${CLIENT_IP[$client]}"
    socks5=$(vm_ssh "$ip" "ss -tnlp | grep ':1080' || true")
    if [[ -n "$socks5" ]]; then
        pass "$client: SOCKS5 port 1080 listening"
    else
        fail "$client: SOCKS5 port 1080 NOT listening — nym5 traffic will fail"
        if $FIX_NYM; then
            echo "    [fix] enabling SOCKS5..."
            vm_ssh "$ip" \
                "nym-vpnc socks5 disable || true; sleep 1; \
                 nym-vpnc socks5 enable --socks5-address 127.0.0.1:1080 --exit-random 2>&1 | tail -2" \
                || true
            recheck=$(vm_ssh "$ip" "ss -tnlp | grep ':1080' || true")
            if [[ -n "$recheck" ]]; then
                pass "$client: SOCKS5 enabled"
            else
                fail "$client: SOCKS5 enable failed — manual intervention needed"
            fi
        else
            echo "    → run with --fix-nym, or manually:"
            echo "      ssh root@$ip"
            echo "      nym-vpnc socks5 enable --socks5-address 127.0.0.1:1080 --exit-random"
        fi
    fi
done

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════"
echo -e " Results:  ${GREEN}${PASS} PASS${NC}  |  ${RED}${FAIL} FAIL${NC}  |  ${YELLOW}${WARN} WARN${NC}"
echo "════════════════════════════════════════════════════════"

if (( FAIL > 0 )); then
    echo ""
    echo " !! ${FAIL} check(s) FAILED — fix above issues before collecting."
    if ! $FIX_NYM; then
        echo "    Tip: re-run with --fix-nym to attempt automatic Nym reconnects."
    fi
fi

if (( WARN > 0 && FAIL == 0 )); then
    echo ""
    echo " ${WARN} warning(s) — collection may still work, but review above."
fi

# ── Optional: quick end-to-end test ──────────────────────────────────────────
if $RUN_QUICK_TEST; then
    if (( FAIL > 0 )); then
        echo ""
        echo " Skipping quick test because ${FAIL} critical check(s) failed."
    else
        echo ""
        echo "════════════════════════════════════════════════════════"
        echo " Running collect_quick_test.sh (~60 min) ..."
        echo "════════════════════════════════════════════════════════"
        bash "$(dirname "$0")/collect_quick_test.sh"
    fi
fi

echo ""
(( FAIL == 0 ))   # exit 0 if no failures, 1 otherwise
