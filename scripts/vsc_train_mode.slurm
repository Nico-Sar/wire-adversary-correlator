#!/bin/bash
# scripts/vsc_train_mode.slurm
# ==============================
# Trains one mode's DualCNNCorrelator on VSC (genius cluster). One
# classifier per mode -- submit once per mode with MODE set, e.g.:
#
#   sbatch --job-name=wac_train_vpn --export=ALL,MODE=vpn scripts/vsc_train_mode.slurm
#
# Or use scripts/vsc_submit_all.sh to submit all four modes (+ chained
# evaluation jobs) at once.
#
# Prereqs (run first, from the desktop or leroy):
#   bash scripts/vsc_push_code.sh          # pushes model/config/analysis
#   bash scripts/merge_and_stage_mode.sh <mode>   # pushes datasets/<mode>_merged.npz
#
# Partition: gpu_p100 (13 nodes, 3-day limit) -- chosen over gpu_v100
# (faster per-GPU but only 2 nodes) because queue wait time matters more
# than per-epoch speed for this dataset/model size. Override with
# --partition=gpu_v100 (or _long) at submit time if p100 is congested.
#SBATCH --job-name=wac_train
#SBATCH --partition=gpu_p100
#SBATCH --gres=gpu:1
#SBATCH --time=04:00:00
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
[[ -f "$DATASET" ]] || { echo "[ERROR] $DATASET not found -- run merge_and_stage_mode.sh $MODE first" >&2; exit 1; }

echo "[$(date)] Training $MODE on $(hostname), GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null)"

python -m model.train \
    --dataset "$DATASET" \
    --mode "$MODE" \
    --output "results/${MODE}"

echo "TRAIN_EXIT=$?"
