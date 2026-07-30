#!/bin/bash -l
# scripts/vsc_run_wl_baseline.slurm
# ===================================
# Re-verification follow-up (2026-07-30): the pilot window_len sweep's
# selection metric (train.py's in-training val PR-AUC, window_len_sweep_
# train.py) turned out to be computed on a balanced-ish negative-sampling
# validation split, not evaluate.py's full imbalanced all-pairs evaluation
# this thesis actually reports -- it saturated near 1.0 for vpn and missed
# a real ~6x PR-AUC effect only visible under the real metric (see the
# 2026-07-30 note in config/kde_params.py). tor and nym2's window_len=10
# choice, and nym5's "no signal, keep 30" choice, all rested on the same
# flawed proxy. This runs a real 5-seed train+eval cycle (evaluate.py, the
# metric that matters) for three baseline/candidate variants built the same
# way as vpn's wl=10 follow-up -- apples-to-apples against each mode's
# already-real production numbers:
#   tor_wl30:  tor's PRE-retune baseline (real production is currently wl=10)
#   nym2_wl30: nym2's PRE-retune baseline (real production is currently wl=10)
#   nym5_wl40: the pilot's preferred (but never validated) candidate
#              (real production is currently wl=30, unchanged)
# Datasets built from the same raw campaign pcaps as the real datasets,
# only --window differs -- see config/kde_params.py's 2026-07-30 notes and
# /volume1/scratch/r1086364/full_rebuild/run_{tor_wl30,nym2_wl30,nym5_wl40}.sh
# on leroy for the exact build commands.
#
#   sbatch -M mindwell -A lp_pets --array=0-14 scripts/vsc_run_wl_baseline.slurm
#
#SBATCH --job-name=wl_baseline
#SBATCH --partition=batch_graniterapids
#SBATCH --time=08:00:00
#SBATCH --cpus-per-task=32
#SBATCH --mem=96G
# Same OOM class as tor/nym2's original wl=10 retune (more windows/flow than
# the CHUNK_ING=100/mem=32G config was sized for) -- applying the already-
# fixed CHUNK_ING=40/mem=96G preemptively rather than rediscovering it.
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

# variant -> (real mode name, dataset filename under datasets/{mode}/)
VARIANTS=(tor_wl30 nym2_wl30 nym5_wl40)
MODES=(tor nym2 nym5)
DATASETS=(tor_wl30_merged.npz nym2_wl30_merged.npz nym5_wl40_merged.npz)
SEEDS=(1 2 3 4 5)
N_SEEDS=${#SEEDS[@]}

TASK_ID="${SLURM_ARRAY_TASK_ID:?run via sbatch --array=0-14, not directly}"
VARIANT_IDX=$(( TASK_ID / N_SEEDS ))
SEED_IDX=$(( TASK_ID % N_SEEDS ))
VARIANT="${VARIANTS[$VARIANT_IDX]}"
MODE="${MODES[$VARIANT_IDX]}"
DATASET_FILE="${DATASETS[$VARIANT_IDX]}"
SEED="${SEEDS[$SEED_IDX]}"

echo "[$(date)] wl_baseline task $TASK_ID -> variant=$VARIANT mode=$MODE seed=$SEED, on $(hostname), $(nproc) CPUs visible"

module purge
module load Python/3.12.3-GCCcore-13.3.0

BUNDLE_DIR="$VSC_SCRATCH/wire-adversary-correlator"
cd "$BUNDLE_DIR" || { echo "[ERROR] $BUNDLE_DIR not found" >&2; exit 1; }
mkdir -p logs

DATA_ROOT="$VSC_DATA/wire-adversary-correlator/wire-adversary-correlator-data"
DATASET="$DATA_ROOT/datasets/${MODE}/${DATASET_FILE}"
[[ -f "$DATASET" ]] || { echo "[ERROR] $DATASET not found -- git pull the data repo first" >&2; exit 1; }

OUT_DIR="results/${VARIANT}_seed${SEED}"
mkdir -p "$OUT_DIR"

THREADS="$SLURM_CPUS_PER_TASK"

python -m model.train --dataset "$DATASET" --mode "$MODE" --seed "$SEED" --threads "$THREADS" --output "$OUT_DIR"
train_rc=$?
echo "TRAIN_EXIT=$train_rc"
[[ $train_rc -eq 0 ]] || exit $train_rc

python -m model.evaluate --model "$OUT_DIR/${MODE}_best.pt" --dataset "$DATASET" --mode "$MODE" --threads "$THREADS" --output "$OUT_DIR"
eval_rc=$?
echo "EVAL_EXIT=$eval_rc"
exit $eval_rc
