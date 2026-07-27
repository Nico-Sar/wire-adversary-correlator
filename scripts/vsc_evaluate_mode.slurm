#!/bin/bash
# DEPRECATED 2026-07-27: targets genius/gpu_p100 (wrong cluster for this
# account); calls the old model.evaluate that materializes the full
# cross-product instead of streaming it, with no TPR@FPR or ShYSh-style
# PR-AUC-at-base-rates. Use scripts/vsc_run_array.slurm instead (calls the
# rewritten model/evaluate.py). Kept for reference only, do not submit.
#
# scripts/vsc_evaluate_mode.slurm
# =================================
# Evaluates one mode's trained checkpoint on its test split (full
# cross-product, real base rate -- see model/evaluate.py). Normally
# submitted with --dependency=afterok:<train_job_id> right after the
# matching vsc_train_mode.slurm job -- see scripts/vsc_submit_all.sh.
#
#   sbatch --job-name=wac_eval_vpn --export=ALL,MODE=vpn \
#       --dependency=afterok:<train_jobid> scripts/vsc_evaluate_mode.slurm
#
# Cheaper than training (inference only) but the full test cross-product
# can still be large -- same partition/time budget as training, kept
# separate so a training failure doesn't waste an eval slot and vice versa.
#SBATCH --job-name=wac_eval
#SBATCH --partition=gpu_p100
#SBATCH --gres=gpu:1
#SBATCH --time=02:00:00
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --output=%x_%j.out
#SBATCH --error=%x_%j.err

set -uo pipefail

: "${MODE:?set MODE=vpn|tor|nym5|nym2, e.g. sbatch --export=ALL,MODE=vpn ...}"
case "$MODE" in
    vpn|tor|nym5|nym2) ;;
    *) echo "[ERROR] unknown MODE: $MODE (expected vpn|tor|nym5|nym2)" >&2; exit 1 ;;
esac

module purge
module load PyTorch/2.7.1-foss-2024a-CUDA-12.6.0

BUNDLE_DIR="$VSC_SCRATCH/wire-adversary-correlator"
cd "$BUNDLE_DIR" || { echo "[ERROR] $BUNDLE_DIR not found -- run vsc_push_code.sh first" >&2; exit 1; }

DATASET="datasets/${MODE}_merged.npz"
CHECKPOINT="results/${MODE}/${MODE}_best.pt"
[[ -f "$DATASET" ]]   || { echo "[ERROR] $DATASET not found" >&2; exit 1; }
[[ -f "$CHECKPOINT" ]] || { echo "[ERROR] $CHECKPOINT not found -- train $MODE first" >&2; exit 1; }

echo "[$(date)] Evaluating $MODE on $(hostname)"

python -m model.evaluate \
    --model "$CHECKPOINT" \
    --dataset "$DATASET" \
    --mode "$MODE" \
    --output "results/${MODE}"

echo "EVAL_EXIT=$?"
