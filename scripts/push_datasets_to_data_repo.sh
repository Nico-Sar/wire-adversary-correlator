#!/usr/bin/env bash
# scripts/push_datasets_to_data_repo.sh
# ========================================
# Pushes merged per-mode .npz datasets (merge_and_stage_mode.sh output) to
# the GitHub data repo under mode_datasets/, as a relay for getting them
# onto VSC. Direct SSH/rsync from leroy straight into VSC has repeatedly
# hung (confirmed live 2026-07-18/19 — even a plain `ssh vsc echo` can
# time out), while GitHub-over-HTTPS from both leroy and VSC is reliable
# (VSC already has `gh` authenticated as its git credential helper). This
# mirrors the exact same relay pattern already used for the bulk pcap
# backup (see sync_data_repo.sh / batched_push.sh) but targets a small,
# separate path so pulling on VSC doesn't require cloning the whole
# (huge, pcap-filled) data repo -- see scripts/vsc_pull_datasets.sh.
#
# Run on leroy, after merge_and_stage_mode.sh <mode> --skip-vsc has built
# data/campaign/_mode_staging/<mode>/<mode>_merged.npz.
#
# Usage:
#   bash scripts/push_datasets_to_data_repo.sh vpn tor nym2
#   bash scripts/push_datasets_to_data_repo.sh          # all 4 modes, skips any not yet built

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DATA_REPO_DIR="/volume1/scratch/r1086364/data_repo_work/wire-adversary-correlator-data"
STAGING_ROOT="$REPO_ROOT/data/campaign/_mode_staging"

log() { echo "[$(date '+%H:%M:%S')] [push_datasets] $*"; }
die() { echo "[$(date '+%H:%M:%S')] [push_datasets] [ERROR] $*" >&2; exit 1; }

[[ -d "$DATA_REPO_DIR" ]] || die "$DATA_REPO_DIR not found -- data repo staging clone missing"

MODES=("$@")
[[ ${#MODES[@]} -eq 0 ]] && MODES=(vpn tor nym5 nym2)

export GIT_SSH_COMMAND="ssh -i ~/.ssh/data_repo_deploy -o IdentitiesOnly=yes -o BatchMode=yes -o ServerAliveInterval=10 -o ServerAliveCountMax=30 -o TCPKeepAlive=yes"
cd "$DATA_REPO_DIR"
git reset -q

mkdir -p mode_datasets
pushed_any=0
for MODE in "${MODES[@]}"; do
    SRC="$STAGING_ROOT/$MODE/${MODE}_merged.npz"
    if [[ ! -f "$SRC" ]]; then
        log "$MODE: no merged dataset at $SRC yet, skipping"
        continue
    fi
    cp "$SRC" "mode_datasets/${MODE}_merged.npz"
    log "$MODE: copied ($(du -h "mode_datasets/${MODE}_merged.npz" | cut -f1))"
    pushed_any=1
done

if [[ "$pushed_any" -eq 0 ]]; then
    log "nothing to push"
    exit 0
fi

git add mode_datasets/
if git diff --cached --quiet; then
    log "no changes to commit (datasets identical to last push)"
    exit 0
fi

git commit -q -m "data: update mode_datasets/ merged npz ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
for attempt in 1 2 3; do
    if git push origin main --quiet; then
        log "pushed OK (attempt $attempt)"
        exit 0
    fi
    log "push attempt $attempt failed, retrying in 10s"
    sleep 10
done
die "push failed after 3 attempts"
