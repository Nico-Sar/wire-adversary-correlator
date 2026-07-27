#!/usr/bin/env bash
# scripts/vsc_submit_array.sh
# ==============================
# Submits the full 4-mode x 5-seed real-experiment array
# (scripts/vsc_run_array.slurm) in one call.
#
# Run ON VSC, from $VSC_SCRATCH/wire-adversary-correlator (after
# vsc_push_code.sh has staged code there and datasets are staged
# read-only at $VSC_DATA -- see figures/phase1_shapes/README.md):
#   bash scripts/vsc_submit_array.sh
#
# Replaces the 2026-07-18 vsc_submit_all.sh (per-mode single jobs,
# gpu_p100/genius, no seeds).

set -uo pipefail

BUNDLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BUNDLE_DIR"
mkdir -p logs

echo "Submitting 20-task array (4 modes x 5 seeds) to batch_graniterapids..."
out=$(sbatch -M mindwell --array=0-19 scripts/vsc_run_array.slurm)
echo "$out"
jid=$(echo "$out" | awk '{print $NF}')

echo
echo "Track with: squeue -M mindwell -u \$USER"
echo "Per-task logs: logs/wac_array_${jid}_<task_id>.out"
echo
echo "Once all 20 tasks finish, results are at results/{mode}_seed{seed}/{mode}_eval.json"
echo "Aggregate mean+-std per mode with, e.g.:"
echo "  python -c \"
import json, glob, numpy as np
for mode in ['vpn','tor','nym2','nym5']:
    vals = [json.load(open(f))['pr_auc'] for f in sorted(glob.glob(f'results/{mode}_seed*/{mode}_eval.json'))]
    print(mode, 'n=', len(vals), 'mean=', np.mean(vals), 'std=', np.std(vals))
\""
