#!/bin/bash -l
# scripts/vsc_run_vpn_wl10.slurm
# ===============================
# One-off follow-up experiment: does VPN's seed-to-seed VARIANCE (not mean
# PR-AUC, already tested and found no signal, Section~sec:setup) change
# with a shorter window_len (more windows per flow)? The original window_len
# sweep only used 1 seed per candidate, so it could measure mean shift but
# not variance -- this runs the real 5-seed cycle at window_len=10 against
# the SAME full-campaign source as vpn's real (window_len=30) results, so
# the two 5-seed sets are directly comparable on spread, not just mean.
#
# Does NOT replace or touch the real vpn results (vpn_seed1-5); writes to
# results/vpn_wl10_seed{N} instead.
#
#   sbatch -M mindwell -A lp_pets --array=0-4 scripts/vsc_run_vpn_wl10.slurm
#
#SBATCH --job-name=vpn_wl10
#SBATCH --partition=batch_graniterapids
#SBATCH --time=08:00:00
#SBATCH --cpus-per-task=32
#SBATCH --mem=96G
# vpn's window_len 30->10 also roughly triples windows-per-flow (3->11),
# same class of increase that OOM-killed tor/nym2 at 32G -- applying the
# same fix preemptively rather than rediscovering it.
#SBATCH --output=logs/%x_%A_%a.out
#SBATCH --error=logs/%x_%A_%a.err
#SBATCH --signal=B:TERM@180

set -uo pipefail

_requeue() {
    echo "[$(date)] Caught TERM -- requeuing array task $SLURM_ARRAY_TASK_ID"
    scontrol requeue "${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}"
    exit 0
}
trap _requeue TERM

SEEDS=(1 2 3 4 5)
TASK_ID="${SLURM_ARRAY_TASK_ID:?run via sbatch --array=0-4, not directly}"
SEED="${SEEDS[$TASK_ID]}"

echo "[$(date)] vpn_wl10 task $TASK_ID -> seed=$SEED, on $(hostname), $(nproc) CPUs visible"

module purge
module load Python/3.12.3-GCCcore-13.3.0

BUNDLE_DIR="$VSC_SCRATCH/wire-adversary-correlator"
cd "$BUNDLE_DIR" || { echo "[ERROR] $BUNDLE_DIR not found" >&2; exit 1; }
mkdir -p logs

DATASET="$VSC_DATA/wire-adversary-correlator/wire-adversary-correlator-data/datasets/vpn_wl10/vpn_wl10_merged.npz"
[[ -f "$DATASET" ]] || { echo "[ERROR] $DATASET not found -- git pull the data repo first" >&2; exit 1; }

OUT_DIR="results/vpn_wl10_seed${SEED}"
mkdir -p "$OUT_DIR"

THREADS="$SLURM_CPUS_PER_TASK"

python -m model.train --dataset "$DATASET" --mode vpn --seed "$SEED" --threads "$THREADS" --output "$OUT_DIR"
train_rc=$?
echo "TRAIN_EXIT=$train_rc"
[[ $train_rc -eq 0 ]] || exit $train_rc

python -m model.evaluate --model "$OUT_DIR/vpn_best.pt" --dataset "$DATASET" --mode vpn --threads "$THREADS" --output "$OUT_DIR"
eval_rc=$?
echo "EVAL_EXIT=$eval_rc"
exit $eval_rc
