#!/usr/bin/env bash
# scripts/merge_and_stage_mode.sh
# =================================
# READ-ONLY against the live campaign: builds a merged .npz dataset for ONE
# mode from its audit-passed rounds only (never the in-progress round — a
# round with no .audit_passed marker is, by construction, never returned by
# `check_mode_ready.py --list-rounds`), then stages it toward VSC scratch.
# Safe to run while collection is ongoing: never touches data/campaign/,
# collector/, or any campaign script; only touches its own scratch output
# directory and (at the very end) a remote VSC path.
#
# Pipeline per mode:
#   1. scripts/check_mode_ready.py --list-rounds  -> audit-passed round list
#   2. preprocessing/dataset_builder.py once per round -> per-round .npz
#      (data_dir is round_NN/<mode>/, where coordinator.py actually writes
#      pcaps — NOT round_NN/ itself; see collector/coordinator.py's
#      run_single_visit, which does `output_dir / mode / f"{visit_id}_..."`)
#   3. preprocessing/merge_rounds.py -> one merged .npz, with its own
#      split-integrity check against the master URL list
#   4. scripts/kde_shape_check.py -> degeneracy sanity check on the merged
#      output (informational; does not block staging)
#   5. best-effort push to $VSC_SCRATCH via the "vsc" SSH alias, with a
#      post-transfer size check (past sessions have seen VSC transfers
#      silently truncate/corrupt over `scp`/`rsync` — do not trust a clean
#      exit code alone). If VSC is unreachable, the script still succeeds
#      up through step 4 and leaves the merged .npz in the local staging
#      dir with a clear message instead of failing the whole run.
#
# Usage:
#   bash scripts/merge_and_stage_mode.sh <vpn|tor|nym5|nym2> [--skip-vsc]
#
# Output (local, on whatever host this runs on — normally leroy):
#   data/campaign/_mode_staging/<mode>/<mode>_merged.npz
#   data/campaign/_mode_staging/<mode>/round_<NN>.npz   (intermediate, kept
#     for inspection/debugging — safe to delete after a successful merge)

set -uo pipefail

MODE="${1:?usage: merge_and_stage_mode.sh <vpn|tor|nym5|nym2> [--skip-vsc]}"
SKIP_VSC=0
[[ "${2:-}" == "--skip-vsc" ]] && SKIP_VSC=1

case "$MODE" in
    vpn|tor|nym5|nym2) ;;
    *) echo "[ERROR] unknown mode: $MODE (expected vpn|tor|nym5|nym2)" >&2; exit 1 ;;
esac

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

CAMPAIGN_ROOT="$REPO_ROOT/data/campaign"
MASTER_URLS="$CAMPAIGN_ROOT/stage0/validated_urls.txt"
STAGE_DIR="$CAMPAIGN_ROOT/_mode_staging/$MODE"
PYTHON="${COORDINATOR_PYTHON:-$REPO_ROOT/.venv/bin/python3}"

log() { echo "[$(date '+%H:%M:%S')] [merge:$MODE] $*"; }
die() { echo "[$(date '+%H:%M:%S')] [merge:$MODE] [ERROR] $*" >&2; exit 1; }

# Split-instance architecture (matches check_mode_ready.py's
# MODE_POST_SPLIT_DIR / PRE_SPLIT_LAST_ROUND -- keep both in sync): rounds
# 01-03 live under data/campaign; round 04+ restarts numbering independently
# under each split root, vpn/tor/nym2 in data/campaign_fast and nym5 in
# data/campaign_nym5. A bare round number is ambiguous without this.
PRE_SPLIT_LAST_ROUND=3
declare -A POST_SPLIT_DIR=( [vpn]="campaign_fast" [tor]="campaign_fast" [nym2]="campaign_fast" [nym5]="campaign_nym5" )

resolve_round_dir() {
    local round_num="$1" round_padded
    round_padded=$(printf "%02d" "$round_num")
    if (( round_num <= PRE_SPLIT_LAST_ROUND )); then
        echo "$REPO_ROOT/data/campaign/round_${round_padded}"
    else
        echo "$REPO_ROOT/data/${POST_SPLIT_DIR[$MODE]}/round_${round_padded}"
    fi
}

[[ -x "$PYTHON" ]] || die "venv python not found: $PYTHON"
[[ -f "$MASTER_URLS" ]] || die "master URL list not found: $MASTER_URLS"

mkdir -p "$STAGE_DIR"

# ── 1. Audit-passed rounds for this mode ───────────────────────────────────
log "Finding audit-passed rounds for $MODE (never the active round)..."
mapfile -t ROUNDS < <("$PYTHON" scripts/check_mode_ready.py --mode "$MODE" --list-rounds)
if [[ ${#ROUNDS[@]} -eq 0 ]]; then
    die "no audit-passed rounds found for $MODE yet — nothing to merge"
fi
log "Audit-passed rounds: ${ROUNDS[*]}"

# ── 2. Per-round .npz ───────────────────────────────────────────────────────
PER_ROUND_NPZ=()
for r in "${ROUNDS[@]}"; do
    round_padded=$(printf "%02d" "$r")
    round_dir=$(resolve_round_dir "$r")
    labels="$round_dir/${MODE}_visits.jsonl"
    data_dir="$round_dir/$MODE"
    out_npz="$STAGE_DIR/round_${round_padded}.npz"

    if [[ ! -f "$labels" ]]; then
        log "WARNING: $labels not found — skipping round $round_padded for $MODE"
        continue
    fi
    if [[ ! -d "$data_dir" ]]; then
        log "WARNING: $data_dir not found — skipping round $round_padded for $MODE"
        continue
    fi

    if [[ -f "$out_npz" && "$out_npz" -nt "$labels" ]]; then
        log "round $round_padded: $out_npz already up to date — reusing"
    else
        log "round $round_padded: building $out_npz ..."
        if ! "$PYTHON" preprocessing/dataset_builder.py \
                --labels "$labels" --data_dir "$data_dir" \
                --output "$out_npz" --mode "$MODE"; then
            log "WARNING: dataset_builder.py failed for round $round_padded — skipping it"
            continue
        fi
    fi
    PER_ROUND_NPZ+=("$out_npz")
done

[[ ${#PER_ROUND_NPZ[@]} -gt 0 ]] || die "no per-round .npz files were built successfully — nothing to merge"

# ── 3. Merge with split-integrity check ─────────────────────────────────────
MERGED_NPZ="$STAGE_DIR/${MODE}_merged.npz"
log "Merging ${#PER_ROUND_NPZ[@]} round(s) -> $MERGED_NPZ ..."
"$PYTHON" preprocessing/merge_rounds.py \
    --mode "$MODE" --inputs "${PER_ROUND_NPZ[@]}" \
    --output "$MERGED_NPZ" --master-urls "$MASTER_URLS" \
    || die "merge_rounds.py failed — see output above; merged .npz NOT written/trustworthy"

[[ -f "$MERGED_NPZ" ]] || die "merge_rounds.py reported success but $MERGED_NPZ is missing"
log "Merge OK: $MERGED_NPZ ($(du -h "$MERGED_NPZ" | cut -f1))"

# ── 4. Degeneracy sanity check (informational, non-blocking) ───────────────
KDE_CHECK_DIR="$STAGE_DIR/_kde_check"
mkdir -p "$KDE_CHECK_DIR"
rm -f "$KDE_CHECK_DIR"/*.npz
cp "$MERGED_NPZ" "$KDE_CHECK_DIR/"
log "Running kde_shape_check.py (informational — does not block staging)..."
"$PYTHON" scripts/kde_shape_check.py "$KDE_CHECK_DIR" "$KDE_CHECK_DIR" \
    || log "WARNING: kde_shape_check.py failed to run — inspect $MERGED_NPZ manually before trusting it"

# ── 5. Best-effort push to VSC scratch ──────────────────────────────────────
if (( SKIP_VSC )); then
    log "--skip-vsc given — leaving merged dataset at $MERGED_NPZ"
    exit 0
fi

log "Checking VSC reachability (ssh alias 'vsc')..."
if ! ssh -o ConnectTimeout=10 -o BatchMode=yes vsc 'echo ok' >/dev/null 2>&1; then
    log "VSC unreachable from this host right now — merged dataset is ready at"
    log "  $MERGED_NPZ"
    log "Stage it to VSC separately once reachable (e.g. from lex, which has its"
    log "own 'vsc' alias) — do not assume this host has a network path to VSC."
    exit 0
fi

# BUG FIXED (2026-07-18): plain non-interactive `ssh vsc 'echo $VSC_SCRATCH'`
# always returns empty -- VSC only sets VSC_SCRATCH/etc via profile scripts
# a LOGIN shell sources, not for bare non-interactive command execution.
# Confirmed live: this silently made every run hit the "not staging" branch
# below and skip the VSC push entirely, with no hard failure to flag it.
VSC_SCRATCH_REMOTE=$(ssh vsc 'bash -lc "echo \$VSC_SCRATCH"' 2>/dev/null)
if [[ -z "$VSC_SCRATCH_REMOTE" ]]; then
    log "WARNING: \$VSC_SCRATCH is empty on the remote — not staging. Merged"
    log "dataset is still at $MERGED_NPZ."
    exit 0
fi

REMOTE_DIR="$VSC_SCRATCH_REMOTE/wire-adversary-correlator/datasets"
REMOTE_PATH="$REMOTE_DIR/${MODE}_merged.npz"
LOCAL_SIZE=$(stat -c%s "$MERGED_NPZ" 2>/dev/null || stat -f%z "$MERGED_NPZ")

log "Pushing to vsc:$REMOTE_PATH ..."
ssh vsc "mkdir -p '$REMOTE_DIR'" || die "could not create $REMOTE_DIR on VSC"
# Past sessions found scp/rsync to VSC silently truncate files over the
# ControlMaster-piggybacked connection — use the ssh-pipe form and verify
# size afterward rather than trusting a zero exit code alone.
if ! ssh vsc "cat > '$REMOTE_PATH'" < "$MERGED_NPZ"; then
    die "transfer command failed — do NOT assume $REMOTE_PATH is usable"
fi
REMOTE_SIZE=$(ssh vsc "stat -c%s '$REMOTE_PATH' 2>/dev/null || stat -f%z '$REMOTE_PATH'")
if [[ "$REMOTE_SIZE" != "$LOCAL_SIZE" ]]; then
    die "SIZE MISMATCH after transfer: local=$LOCAL_SIZE remote=$REMOTE_SIZE bytes — " \
        "$REMOTE_PATH is corrupt, do not use it. Re-run this script, or investigate the VSC transfer path."
fi
log "Verified: $REMOTE_PATH matches local size ($LOCAL_SIZE bytes). Staging complete."
