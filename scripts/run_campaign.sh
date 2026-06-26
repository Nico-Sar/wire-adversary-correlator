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

[[ -f "$VALIDATED_FULL" ]]  || die "full URLs file not found: $VALIDATED_FULL — run scripts/validate_urls.sh first"
[[ -f "$VALIDATED_LIGHT" ]] || die "light URLs file not found: $VALIDATED_LIGHT"

log "Agent check..."
ssh-add -l 2>/dev/null | grep -qi "nico-thesis\|nicolas-thesis" \
    || die "ssh-agent does not have ~/.ssh/nico-thesis loaded. Run: eval \"\$(ssh-agent -s)\" && ssh-add ~/.ssh/nico-thesis"

[[ -n "${VISITS_LIGHT:-}" ]] || die "VISITS_LIGHT is not set. Decide visits/URL for nym5/nym2 first (see docs/CAMPAIGN_RUNBOOK.md 'Light-list visits/URL decision'), then: VISITS_LIGHT=<N> bash scripts/run_campaign.sh ..."
export VISITS_LIGHT
log "VISITS_LIGHT=$VISITS_LIGHT (nym5/nym2 visits/client/URL)"

log "Building split-consistent stage grids (full + light) from validated lists..."
python3 scripts/_stage_slices.py "$VALIDATED_FULL" "$VALIDATED_LIGHT" "$SLICE_DIR" 50

n_full=$(find "$SLICE_DIR/full"  -maxdepth 1 -name 'stage_*.txt' 2>/dev/null | wc -l)
n_light=$(find "$SLICE_DIR/light" -maxdepth 1 -name 'stage_*.txt' 2>/dev/null | wc -l)
n_rounds=$(( n_full > n_light ? n_full : n_light ))
log "Full-list stages: $n_full. Light-list stages: $n_light. Total rounds: $n_rounds"
log "(full and light stage N are NOT the same URLs — see split_consistency_check.txt)"

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
    light_stage="$SLICE_DIR/light/stage_${round}.txt"
    [[ -f "$full_stage" ]]  || full_stage="NONE"
    [[ -f "$light_stage" ]] || light_stage="NONE"

    if [[ "$full_stage" == "NONE" && "$light_stage" == "NONE" ]]; then
        log "Round $round: both grids exhausted, nothing to do — should not happen (n_rounds miscount?)"
        continue
    fi

    log "=========================================================="
    log "ROUND $round / $n_rounds — full=$full_stage light=$light_stage"
    log "=========================================================="

    if ! bash scripts/run_stage.sh "$full_stage" "$light_stage" "$round_out" "round_$round"; then
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
