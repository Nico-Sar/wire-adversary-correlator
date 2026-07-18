#!/usr/bin/env bash
# scripts/vsc_push_code.sh
# =========================
# Pushes the code needed to train/evaluate on VSC (model/, config/,
# analysis/ -- NOT collector/, data/, or anything pcap-related) to
# $VSC_SCRATCH/wire-adversary-correlator/ on the 'vsc' SSH alias. Lands in
# the same root merge_and_stage_mode.sh already pushes datasets/ into, so
# a SLURM job on VSC finds both code and data under one directory.
#
# Safe to run from leroy or the desktop repo -- read-only against the
# source tree, only touches the remote path.
#
# Usage:
#   bash scripts/vsc_push_code.sh

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

log() { echo "[$(date '+%H:%M:%S')] [vsc_push_code] $*"; }
die() { echo "[$(date '+%H:%M:%S')] [vsc_push_code] [ERROR] $*" >&2; exit 1; }

log "Checking VSC reachability (ssh alias 'vsc')..."
ssh -o ConnectTimeout=10 -o BatchMode=yes vsc 'echo ok' >/dev/null 2>&1 \
    || die "VSC unreachable from this host right now."

# Plain non-interactive `ssh vsc 'echo $VSC_SCRATCH'` returns empty -- VSC
# only sets VSC_SCRATCH/etc via profile scripts sourced by a LOGIN shell,
# not for bare command execution. Confirmed live (2026-07-18): this same
# mistake in merge_and_stage_mode.sh made it silently report "VSC
# unreachable" and skip staging on every run, no hard failure either.
VSC_SCRATCH_REMOTE=$(ssh vsc 'bash -lc "echo \$VSC_SCRATCH"' 2>/dev/null)
[[ -n "$VSC_SCRATCH_REMOTE" ]] || die "\$VSC_SCRATCH is empty on the remote (even via login shell)."

REMOTE_ROOT="$VSC_SCRATCH_REMOTE/wire-adversary-correlator"
log "Pushing model/, config/, analysis/ to vsc:$REMOTE_ROOT ..."

for dir in model config analysis; do
    [[ -d "$dir" ]] || { log "WARNING: $dir/ not found locally, skipping"; continue; }
    ssh vsc "mkdir -p '$REMOTE_ROOT/$dir'" || die "could not create $REMOTE_ROOT/$dir"
    rsync -az --exclude='__pycache__' --exclude='*.pyc' \
        -e ssh "$dir"/ "vsc:$REMOTE_ROOT/$dir"/ \
        || die "rsync failed for $dir/"
done

# Package markers -- train.py/evaluate.py import as "from config.hyperparams
# import ..." / "from model.cnn import ...", both must be sibling packages
# with __init__.py at the run root (see the 2026-07-13 bundle MANIFEST.txt
# note on this same requirement).
for pkg in model config analysis; do
    ssh vsc "touch '$REMOTE_ROOT/$pkg/__init__.py'" 2>/dev/null || true
done

ssh vsc "mkdir -p '$REMOTE_ROOT/results' '$REMOTE_ROOT/scripts'"

log "Pushing SLURM job scripts..."
scp -q scripts/vsc_train_mode.slurm scripts/vsc_evaluate_mode.slurm scripts/vsc_submit_all.sh \
    "vsc:$REMOTE_ROOT/scripts/" || die "scp of SLURM scripts failed"
ssh vsc "chmod +x '$REMOTE_ROOT/scripts/vsc_submit_all.sh'"

log "Done. Code is at vsc:$REMOTE_ROOT/{model,config,analysis,scripts}, results/ ready for checkpoints."
log "Once datasets are staged (merge_and_stage_mode.sh) and you have SLURM credits, run:"
log "  ssh vsc 'cd $REMOTE_ROOT && bash scripts/vsc_submit_all.sh'"
