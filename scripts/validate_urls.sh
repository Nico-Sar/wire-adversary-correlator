#!/usr/bin/env bash
# scripts/validate_urls.sh
# =========================
# STAGE 0 of the nym campaign. Run this FIRST, before any collection.
#
# Tests every candidate URL against the REAL endpoint each mode's visits
# actually hit, and keeps only URLs that return real content (HTTP 200 +
# size above a floor) on ALL FOUR targets:
#   vpn  -> http://10.1.0.3:8080/<path>      (private webserver IP, curled
#                                              from the egress router — vpn
#                                              traffic already arrives there
#                                              decrypted, see
#                                              config/infrastructure.py URL_BASE)
#   tor  -> http://204.168.189.97:8081/<path> (egress router's public IP,
#   nym5 -> http://204.168.189.97:8082/<path>  curled directly from THIS
#   nym2 -> http://204.168.189.97/<path>        host — not from the egress
#                                                 router itself, which hits
#                                                 a hairpin-NAT failure; see
#                                                 the check_infrastructure()
#                                                 fix in collector/coordinator.py)
#
# This is a quick direct curl per URL/target, NOT a full Playwright visit —
# it validates web-server content correctness, not anonymity-network
# behavior. It does NOT validate that a file is small enough to fetch
# quickly through nym5/nym2's slow path — see the binary-file note in the
# report output and the campaign README.
#
# Usage:
#   bash scripts/validate_urls.sh [candidates_file] [output_dir]
#   bash scripts/validate_urls.sh config/urls.txt data/campaign/stage0
#
# Outputs (in output_dir):
#   validated_urls.txt   — bare paths that passed on all 4 targets, sorted
#                           alphabetically (this exact ordering is what the
#                           campaign's stage-cut math assumes — see
#                           scripts/run_campaign.sh)
#   validation_report.txt — pass/fail counts, per-mode failures with reason,
#                           per-extension breakdown

set -uo pipefail   # no -e: a single curl failure must not abort the sweep

CANDIDATES="${1:-config/urls.txt}"
OUTDIR="${2:-data/campaign/stage0}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/nico-thesis}"
SSH_OPTS="-i $SSH_KEY -o StrictHostKeyChecking=no -o ConnectTimeout=10 -o BatchMode=yes"
EGRESS_IP="204.168.189.97"
WEBSERVER_PRIVATE_IP="10.1.0.3"
MIN_BYTES=200          # sane floor: real content clears this, 404/empty pages don't
CURL_TIMEOUT=15
PARALLEL=10

mkdir -p "$OUTDIR"
VALIDATED="$OUTDIR/validated_urls.txt"
REPORT="$OUTDIR/validation_report.txt"
WORKDIR="$OUTDIR/_work"
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# ── Pre-flight ────────────────────────────────────────────────────────────────
[[ -f "$CANDIDATES" ]] || { echo "[error] candidates file not found: $CANDIDATES"; exit 1; }

log "Checking ssh-agent has the campaign key loaded..."
if ! ssh-add -l 2>/dev/null | grep -qi "nico-thesis\|nicolas-thesis"; then
    echo "[error] ~/.ssh/nico-thesis is not loaded in a running ssh-agent."
    echo "        Run: eval \"\$(ssh-agent -s)\" && ssh-add $SSH_KEY"
    exit 1
fi

log "Checking egress router reachable ($EGRESS_IP)..."
if ! ssh $SSH_OPTS "root@$EGRESS_IP" 'echo ok' >/dev/null 2>&1; then
    echo "[error] cannot reach egress router at $EGRESS_IP — fix before validating."
    exit 1
fi

# ── Doubled-URL guard ─────────────────────────────────────────────────────────
# A candidates file with a full http://... entry gets concatenated onto the
# mode's URL_BASE by coordinator.py (url_base + "/" + line), producing a
# malformed double-URL that silently 404s as "garbage success". Catch this
# here, at the source, before it ever reaches a stage.
bad_lines=$(grep -nE '^[[:space:]]*https?://' "$CANDIDATES" || true)
if [[ -n "$bad_lines" ]]; then
    echo "[error] $CANDIDATES contains full URLs, not bare paths — coordinator.py"
    echo "        builds URLs as urls_base + \"/\" + line, so these would double up:"
    echo "$bad_lines" | sed 's/^/    /'
    exit 1
fi

mapfile -t CANDIDATE_URLS < <(grep -v '^[[:space:]]*#' "$CANDIDATES" | grep -v '^[[:space:]]*$')
log "Candidates: ${#CANDIDATE_URLS[@]}"

# ── Per-URL, per-target check ──────────────────────────────────────────────────
# Writes one result line per URL to $WORKDIR/<sanitized-url>.result :
#   "<url>\t<vpn_result>\t<tor_result>\t<nym5_result>\t<nym2_result>"
# where each result is "OK" or "FAIL:<reason>".
check_one() {
    local url="$1"
    local safe="${url//\//_}"

    # vpn — via egress router, private IP (no hairpin: egress router really
    # is on the same LAN as the webserver)
    local vpn_code vpn_size vpn_result
    read -r vpn_code vpn_size < <(
        ssh $SSH_OPTS "root@$EGRESS_IP" \
            "curl -s -o /tmp/_validate_body -w '%{http_code} %{size_download}' \
             --max-time $CURL_TIMEOUT 'http://$WEBSERVER_PRIVATE_IP:8080/$url' 2>/dev/null \
             || echo '000 0'"
    )
    if [[ "$vpn_code" == "200" && "$vpn_size" -ge "$MIN_BYTES" ]]; then
        vpn_result="OK"
    elif [[ "$vpn_code" == "200" ]]; then
        vpn_result="FAIL:tiny(${vpn_size}B)"
    else
        vpn_result="FAIL:http${vpn_code}"
    fi

    # tor / nym5 / nym2 — directly from this host (leroy), public egress IP.
    # Curling from the egress router itself to its OWN public IP is the
    # hairpin case that fails outright (see collector/coordinator.py) — this
    # host is external, so no hairpin issue.
    local tor_result nym5_result nym2_result
    for spec in "tor:8081" "nym5:8082" "nym2:"; do
        local mode="${spec%%:*}" port="${spec##*:}"
        local target="http://$EGRESS_IP${port:+:$port}/$url"
        local code size
        read -r code size < <(
            curl -s -o /tmp/_validate_body_$$ -w '%{http_code} %{size_download}' \
                --max-time "$CURL_TIMEOUT" "$target" 2>/dev/null || echo '000 0'
        )
        rm -f /tmp/_validate_body_$$
        local result
        if [[ "$code" == "200" && "$size" -ge "$MIN_BYTES" ]]; then
            result="OK"
        elif [[ "$code" == "200" ]]; then
            result="FAIL:tiny(${size}B)"
        else
            result="FAIL:http${code}"
        fi
        case "$mode" in
            tor)  tor_result="$result" ;;
            nym5) nym5_result="$result" ;;
            nym2) nym2_result="$result" ;;
        esac
    done

    printf '%s\t%s\t%s\t%s\t%s\n' "$url" "$vpn_result" "$tor_result" "$nym5_result" "$nym2_result" \
        > "$WORKDIR/$safe.result"
}
export -f check_one
export SSH_OPTS EGRESS_IP WEBSERVER_PRIVATE_IP MIN_BYTES CURL_TIMEOUT WORKDIR

log "Checking ${#CANDIDATE_URLS[@]} URLs against vpn/tor/nym5/nym2 (parallel=$PARALLEL)..."
printf '%s\n' "${CANDIDATE_URLS[@]}" | xargs -P "$PARALLEL" -I{} bash -c 'check_one "$@"' _ {}

# ── Aggregate ──────────────────────────────────────────────────────────────────
ALL_RESULTS="$WORKDIR/_all.tsv"
cat "$WORKDIR"/*.result > "$ALL_RESULTS" 2>/dev/null

n_total=${#CANDIDATE_URLS[@]}
n_pass=$(awk -F'\t' '$2=="OK" && $3=="OK" && $4=="OK" && $5=="OK"' "$ALL_RESULTS" | wc -l)
n_fail=$((n_total - n_pass))

{
    echo "URL VALIDATION REPORT — $(date '+%Y-%m-%d %H:%M:%S')"
    echo "candidates file: $CANDIDATES"
    echo "================================================================"
    echo "Total candidates:        $n_total"
    echo "Passed on ALL 4 targets: $n_pass"
    echo "Failed on >=1 target:    $n_fail"
    echo ""
    echo "--- Per-target failure counts ---"
    for i in 2 3 4 5; do
        case "$i" in 2) m=vpn;; 3) m=tor;; 4) m=nym5;; 5) m=nym2;; esac
        n=$(awk -F'\t' -v c="$i" '$c!="OK"' "$ALL_RESULTS" | wc -l)
        echo "  $m: $n failures"
    done
    echo ""
    echo "--- Failures, by URL (reason per target) ---"
    awk -F'\t' '!($2=="OK" && $3=="OK" && $4=="OK" && $5=="OK") {
        printf "  %-40s vpn=%-14s tor=%-14s nym5=%-14s nym2=%-14s\n", $1, $2, $3, $4, $5
    }' "$ALL_RESULTS"
    echo ""
    echo "--- Pass/fail by file extension (heavy-binary risk for nym5/nym2) ---"
    echo "  NOTE: this validation only checks content correctness via a quick"
    echo "  direct curl. It does NOT test fetch time through the slow nym5/nym2"
    echo "  path — large mp3/mp4/pdf/zip files can pass here and still blow the"
    echo "  per-visit time budget when fetched through 5-hop mixnet or 2-hop"
    echo "  WireGuard. Review this breakdown before deciding whether to exclude"
    echo "  binary extensions from the nym5/nym2 slice of the shared URL list."
    for ext in html json mp3 mp4 pdf zip; do
        total_ext=$(printf '%s\n' "${CANDIDATE_URLS[@]}" | grep -c "\.${ext}$" || true)
        pass_ext=$(awk -F'\t' -v e=".$ext" '$1 ~ e"$" && $2=="OK" && $3=="OK" && $4=="OK" && $5=="OK"' "$ALL_RESULTS" | wc -l)
        [[ "$total_ext" -gt 0 ]] && echo "  .$ext: $pass_ext / $total_ext passed"
    done
} | tee "$REPORT"

# Validated list, sorted alphabetically — this exact ordering is what
# scripts/_stage_slices.py's stage-cut math assumes (matches model/dataset.py's
# sorted(set(urls)) split logic).
awk -F'\t' '$2=="OK" && $3=="OK" && $4=="OK" && $5=="OK" {print $1}' "$ALL_RESULTS" \
    | sort > "$VALIDATED"

# Light subset (nym5/nym2): html+json only, a strict subset of $VALIDATED by
# construction (same grep filter, same source file) — no re-fetch needed,
# these URLs already passed all 4 targets above. Heavy mp3/mp4/pdf/zip are
# too slow/timeout-prone through nym5's 5-hop path (NS_ERROR_NET_TIMEOUT
# observed in testing) — see docs/CAMPAIGN_RUNBOOK.md for the per-mode
# design this feeds into.
VALIDATED_LIGHT="$OUTDIR/validated_urls_light.txt"
grep -E '\.(html|json)$' "$VALIDATED" > "$VALIDATED_LIGHT" || true
n_light=$(wc -l < "$VALIDATED_LIGHT")

rm -rf "$WORKDIR"

echo ""
echo "================================================================"
echo " Validated URL list (full, vpn/tor):   $VALIDATED  ($n_pass URLs)"
echo " Validated URL list (light, nym5/nym2): $VALIDATED_LIGHT  ($n_light URLs)"
echo " Full report:                           $REPORT"
echo "================================================================"
if [[ "$n_pass" -lt 500 ]]; then
    echo ""
    echo "[decision needed] $n_pass / 500 URLs passed. Review $REPORT, then either:"
    echo "  - fix the web server / failing URLs and re-run this script, or"
    echo "  - proceed with $n_pass URLs (campaign math in run_campaign.sh must be"
    echo "    re-derived for this count, not assumed to be 500)."
fi
