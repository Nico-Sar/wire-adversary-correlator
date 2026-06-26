#!/usr/bin/env bash
# scripts/run_campaign.sh
# =========================
# Orchestrates the full nym flow-correlation campaign: stage 1..N, with an
# audit_stage.sh gate after every stage that HALTS the campaign (does not
# auto-proceed) on any red flag.
#
# Prerequisites (run separately, BEFORE this):
#   1. eval "$(ssh-agent -s)" && ssh-add ~/.ssh/nico-thesis
#   2. bash scripts/validate_urls.sh config/urls.txt data/campaign/stage0
#   3. Review data/campaign/stage0/validation_report.txt
#
# Usage:
#   bash scripts/run_campaign.sh <validated_urls.txt> <campaign_root> <license_deadline_YYYY-MM-DD> [start_stage]
#
# Resumability: each stage's coordinator runs already resume by serial from
# their own {mode}_visits.jsonl (existing coordinator.py behavior — confirmed
# in collector/coordinator.py's run_dataset resume logic, unchanged here).
# Re-running this script with the same campaign_root and a start_stage at or
# before the interrupted stage is safe: completed visits are skipped, not
# redone. Stage boundaries (this script's loop) are the checkpoint unit —
# a stage that already PASSED audit is not re-run unless you force it by
# passing an earlier start_stage explicitly.

set -uo pipefail

VALIDATED_URLS="${1:?usage: run_campaign.sh <validated_urls.txt> <campaign_root> <license_deadline_YYYY-MM-DD> [start_stage]}"
CAMPAIGN_ROOT="${2:?usage: run_campaign.sh <validated_urls.txt> <campaign_root> <license_deadline_YYYY-MM-DD> [start_stage]}"
LICENSE_DEADLINE="${3:?usage: run_campaign.sh <validated_urls.txt> <campaign_root> <license_deadline_YYYY-MM-DD> [start_stage]}"
START_STAGE="${4:-1}"

SLICE_DIR="$CAMPAIGN_ROOT/_url_slices"
mkdir -p "$CAMPAIGN_ROOT"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [campaign] $*"; }
die() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [campaign] [ERROR] $*" >&2; exit 1; }

[[ -f "$VALIDATED_URLS" ]] || die "validated URLs file not found: $VALIDATED_URLS — run scripts/validate_urls.sh first"

log "Agent check..."
ssh-add -l 2>/dev/null | grep -qi "nico-thesis\|nicolas-thesis" \
    || die "ssh-agent does not have ~/.ssh/nico-thesis loaded. Run: eval \"\$(ssh-agent -s)\" && ssh-add ~/.ssh/nico-thesis"

log "Building stage slices (split-aligned, ~50 URLs/stage) from $VALIDATED_URLS..."
python3 scripts/_stage_slices.py "$VALIDATED_URLS" "$SLICE_DIR" 50

n_stages=$(find "$SLICE_DIR" -maxdepth 1 -name 'stage_*.txt' | wc -l)
log "Total stages: $n_stages"

for stage_num in $(seq -w 1 "$n_stages"); do
    if (( 10#$stage_num < START_STAGE )); then
        log "Skipping stage $stage_num (before start_stage=$START_STAGE)"
        continue
    fi

    stage_urls="$SLICE_DIR/stage_${stage_num}.txt"
    stage_out="$CAMPAIGN_ROOT/stage_${stage_num}"

    if [[ -f "$stage_out/.audit_passed" ]]; then
        log "Stage $stage_num already passed audit (found $stage_out/.audit_passed) — skipping"
        continue
    fi

    log "=========================================================="
    log "STAGE $stage_num / $n_stages — $(wc -l < "$stage_urls") URLs"
    log "=========================================================="

    if ! bash scripts/run_stage.sh "$stage_urls" "$stage_out" "stage_$stage_num"; then
        die "stage $stage_num could not be launched at all (see output above) — campaign halted"
    fi

    log "Stage $stage_num collection done. Running audit gate..."
    if bash scripts/audit_stage.sh "$stage_out" "$CAMPAIGN_ROOT" "$LICENSE_DEADLINE"; then
        touch "$stage_out/.audit_passed"
        log "Stage $stage_num PASSED audit. Proceeding."
    else
        die "Stage $stage_num FAILED audit (see report above). Campaign HALTED — review before re-running. Re-run with start_stage=$stage_num to retry this stage after fixing the issue."
    fi
done

log "=========================================================="
log "Campaign complete: all $n_stages stages passed audit."
log "=========================================================="
