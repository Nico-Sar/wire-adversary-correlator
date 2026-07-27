#!/usr/bin/env bash
# DEPRECATED 2026-07-27: submits the old per-mode-only (no seed) jobs to
# genius/gpu_p100. Use scripts/vsc_submit_array.sh instead (4 modes x 5
# seeds, targets mindwell/batch_graniterapids). Kept for reference only.
#
# scripts/vsc_submit_all.sh
# ===========================
# Submits train -> evaluate (SLURM dependency chain) for every mode that
# has a merged dataset staged on VSC. One classifier per mode, run
# independently -- a slow/failed mode doesn't block the others.
#
# Run this ON VSC (from $VSC_SCRATCH/wire-adversary-correlator, after
# vsc_push_code.sh and merge_and_stage_mode.sh have staged code + data),
# or invoke remotely:
#   ssh vsc 'cd $VSC_SCRATCH/wire-adversary-correlator && bash scripts/vsc_submit_all.sh'
#
# Usage:
#   bash scripts/vsc_submit_all.sh [mode ...]   # default: vpn tor nym5 nym2
#   bash scripts/vsc_submit_all.sh vpn tor      # only these modes

set -uo pipefail

MODES=("${@:-vpn tor nym5 nym2}")
[[ $# -gt 0 ]] && MODES=("$@")
[[ $# -eq 0 ]] && MODES=(vpn tor nym5 nym2)

BUNDLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BUNDLE_DIR"

for MODE in "${MODES[@]}"; do
    case "$MODE" in
        vpn|tor|nym5|nym2) ;;
        *) echo "[ERROR] unknown mode: $MODE (expected vpn|tor|nym5|nym2)" >&2; continue ;;
    esac

    DATASET="datasets/${MODE}_merged.npz"
    if [[ ! -f "$DATASET" ]]; then
        echo "[skip] $MODE: $DATASET not staged yet (run merge_and_stage_mode.sh $MODE first)"
        continue
    fi

    train_out=$(sbatch --job-name="wac_train_${MODE}" --export=ALL,MODE="$MODE" scripts/vsc_train_mode.slurm)
    train_jid=$(echo "$train_out" | awk '{print $NF}')
    echo "[$MODE] train job: $train_jid"

    eval_out=$(sbatch --job-name="wac_eval_${MODE}" --export=ALL,MODE="$MODE" \
        --dependency="afterok:${train_jid}" scripts/vsc_evaluate_mode.slurm)
    eval_jid=$(echo "$eval_out" | awk '{print $NF}')
    echo "[$MODE] eval job:  $eval_jid (runs after $train_jid succeeds)"
done

echo
echo "Track with: squeue -u \$USER"
echo "Once all eval jobs finish, build the comparison figure with:"
echo "  python -m analysis.compare_systems --vpn results/vpn/vpn_eval.json \\"
echo "      --tor results/tor/tor_eval.json --nym5 results/nym5/nym5_eval.json \\"
echo "      --nym2 results/nym2/nym2_eval.json --output figures/system_comparison"
