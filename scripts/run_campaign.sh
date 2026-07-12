#!/usr/bin/env bash
# scripts/run_campaign.sh
# =========================
# Orchestrates the full nym flow-correlation campaign with the per-mode URL
# design: vpn/tor against the full validated list, nym5/nym2 against the
# lighter html+json subset. These are TWO INDEPENDENT stage grids (see
# scripts/_stage_slices.py — different URL counts chunk differently; the
# light grid is shorter). This orchestrator advances both grids concurrently
# by index: round i runs full-stage i (if it still exists) concurrently with
# light-stage i (if it still exists), via one scripts/run_stage.sh call, then
# audits whatever ran. Once one grid is exhausted, the other keeps running
# alone for its remaining rounds.
#
# Prerequisites (run separately, BEFORE this):
#   1. eval "$(ssh-agent -s)" && ssh-add ~/.ssh/nico-thesis
#   2. bash scripts/validate_urls.sh config/urls.txt data/campaign/stage0
#   3. Review data/campaign/stage0/validation_report.txt
#   4. Decide VISITS_LIGHT (visits/client/URL for nym5/nym2) — see
#      docs/CAMPAIGN_RUNBOOK.md "Light-list visits/URL decision". There is
#      NO default; this script requires it explicitly (env var) before
#      touching any light-list stage.
#
# Usage:
#   VISITS_LIGHT=<N> bash scripts/run_campaign.sh \
#       <validated_full.txt> <validated_light.txt> <campaign_root> \
#       <license_deadline_YYYY-MM-DD> [start_round]
#
# Resumability: each stage's coordinator runs already resume by serial from
# their own {mode}_visits.jsonl (existing coordinator.py behavior, unchanged
# here). Round boundaries (this script's loop) are the checkpoint unit — a
# round already marked .audit_passed is skipped on re-run.

set -uo pipefail

VALIDATED_FULL="${1:?usage: run_campaign.sh <full.txt> <light.txt> <campaign_root> <deadline> [start_round]}"
VALIDATED_LIGHT="${2:?usage: run_campaign.sh <full.txt> <light.txt> <campaign_root> <deadline> [start_round]}"
CAMPAIGN_ROOT="${3:?usage: run_campaign.sh <full.txt> <light.txt> <campaign_root> <deadline> [start_round]}"
LICENSE_DEADLINE="${4:?usage: run_campaign.sh <full.txt> <light.txt> <campaign_root> <deadline> [start_round]}"
START_ROUND="${5:-1}"

SLICE_DIR="$CAMPAIGN_ROOT/_url_slices"
mkdir -p "$CAMPAIGN_ROOT"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [campaign] $*"; }
die() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [campaign] [ERROR] $*" >&2; exit 1; }

# PROPOSED (2026-07-12, applying patches/10_nym5_instance_separation_design.md):
# MODE_SCOPE=both|nym5|fast lets this script drive only one instance, so
# nym5 and the vpn/tor/nym2 trio can run as two fully independent
# instances (separate CAMPAIGN_ROOT, separate round-dir numbering,
# separate .audit_passed markers) — neither waits on the other to close a
# round anymore. Default "both" reproduces the exact previous behavior
# unchanged (single-instance mode still works, e.g. for the one-time
# round_03 transitional handling described in the design doc, or
# manual/ad-hoc runs). Exported so run_stage.sh (which needs the same
# value to separate nym5 from nym2 — they share the light grid) inherits
# it automatically.
MODE_SCOPE="${MODE_SCOPE:-both}"
case "$MODE_SCOPE" in
    both|nym5|fast) ;;
    *) die "invalid MODE_SCOPE='$MODE_SCOPE' (expected both|nym5|fast)" ;;
esac
export MODE_SCOPE

[[ -f "$VALIDATED_FULL" ]]  || die "full URLs file not found: $VALIDATED_FULL — run scripts/validate_urls.sh first"
[[ -f "$VALIDATED_LIGHT" ]] || die "light URLs file not found: $VALIDATED_LIGHT"

log "Agent check..."
ssh-add -l 2>/dev/null | grep -qi "nico-thesis\|nicolas-thesis" \
    || die "ssh-agent does not have ~/.ssh/nico-thesis loaded. Run: eval \"\$(ssh-agent -s)\" && ssh-add ~/.ssh/nico-thesis"

[[ -n "${VISITS_LIGHT:-}" ]] || die "VISITS_LIGHT is not set. Decide visits/URL for nym5/nym2 first (see docs/CAMPAIGN_RUNBOOK.md 'Light-list visits/URL decision'), then: VISITS_LIGHT=<N> bash scripts/run_campaign.sh ..."
export VISITS_LIGHT
log "VISITS_LIGHT=$VISITS_LIGHT (nym5/nym2 visits/client/URL)"

log "Building split-consistent stage grids (full + tor + light) from validated lists..."
python3 scripts/_stage_slices.py "$VALIDATED_FULL" "$VALIDATED_LIGHT" "$SLICE_DIR" 50

n_full=$(find "$SLICE_DIR/full"  -maxdepth 1 -name 'stage_*.txt' 2>/dev/null | wc -l)
n_tor=$(find  "$SLICE_DIR/tor"   -maxdepth 1 -name 'stage_*.txt' 2>/dev/null | wc -l)
n_light=$(find "$SLICE_DIR/light" -maxdepth 1 -name 'stage_*.txt' 2>/dev/null | wc -l)
# PROPOSED: n_rounds is scoped to whichever grid(s) this instance actually
# drives. nym5 only ever touches the light grid, so no reason to iterate
# past its own 7 stages. "fast" touches full+tor+ (nym2's share of) light,
# same as "both" — light naturally goes NONE once its 7 stages are
# exhausted (see the per-round NONE-on-absence check below), so max() is
# still correct there, not an overcount.
case "$MODE_SCOPE" in
    nym5) n_rounds=$n_light ;;
    fast) n_rounds=$(( n_full > n_light ? n_full : n_light )) ;;
    both) n_rounds=$(( n_full > n_light ? n_full : n_light )) ;;
esac
log "Full-list (vpn) stages: $n_full. Tor (full-minus-zip) stages: $n_tor. Light (nym5/nym2) stages: $n_light. Total rounds: $n_rounds (MODE_SCOPE=$MODE_SCOPE)"
log "(full/tor share stage index; missing tor/stage_NN → tor=NONE that round. light is independent.)"

for round in $(seq -w 1 "$n_rounds"); do
    if (( 10#$round < START_ROUND )); then
        log "Skipping round $round (before start_round=$START_ROUND)"
        continue
    fi

    round_out="$CAMPAIGN_ROOT/round_${round}"
    if [[ -f "$round_out/.audit_passed" ]]; then
        log "Round $round already passed audit — skipping"
        continue
    fi

    full_stage="$SLICE_DIR/full/stage_${round}.txt"
    tor_stage="$SLICE_DIR/tor/stage_${round}.txt"
    light_stage="$SLICE_DIR/light/stage_${round}.txt"
    [[ -f "$full_stage" ]]                    || full_stage="NONE"
    [[ -f "$tor_stage" && -s "$tor_stage" ]]  || tor_stage="NONE"
    [[ -f "$light_stage" ]]                   || light_stage="NONE"
    # PROPOSED: force full/tor to NONE for the nym5-only instance,
    # regardless of file existence — without this it would also launch
    # vpn/tor every round, since those stage files always exist once
    # _stage_slices.py has run. The "fast" instance does NOT force
    # light_stage to NONE here — nym2 needs it active (nym2 travels with
    # vpn/tor, not with nym5, despite sharing the light URL grid); light
    # naturally goes NONE on its own once exhausted (7 stages), same as
    # any other grid, no forcing needed. Which client (nym5 vs nym2)
    # actually launches when light IS active is decided by run_stage.sh's
    # own MODE_SCOPE-driven client selection, not here.
    if [[ "$MODE_SCOPE" == "nym5" ]]; then
        full_stage="NONE"; tor_stage="NONE"
    fi

    if [[ "$full_stage" == "NONE" && "$tor_stage" == "NONE" && "$light_stage" == "NONE" ]]; then
        log "Round $round: all grids exhausted, nothing to do — should not happen (n_rounds miscount?)"
        continue
    fi

    log "=========================================================="
    log "ROUND $round / $n_rounds — vpn=$full_stage tor=$tor_stage light=$light_stage"
    log "=========================================================="

    if ! TOR_URLS="$tor_stage" bash scripts/run_stage.sh "$full_stage" "$light_stage" "$round_out" "round_$round"; then
        die "round $round could not be launched at all (see output above) — campaign halted"
    fi

    log "Round $round collection done. Running audit gate..."
    if bash scripts/audit_stage.sh "$round_out" "$CAMPAIGN_ROOT" "$LICENSE_DEADLINE"; then
        touch "$round_out/.audit_passed"
        log "Round $round PASSED audit. Proceeding."
    else
        die "Round $round FAILED audit (see report above). Campaign HALTED — review before re-running. Re-run with start_round=$round to retry after fixing the issue."
    fi
done

log "=========================================================="
log "Campaign complete: all $n_rounds rounds passed audit."
log "=========================================================="
