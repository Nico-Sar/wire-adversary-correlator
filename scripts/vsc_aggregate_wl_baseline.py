"""
scripts/vsc_aggregate_wl_baseline.py
=====================================
Aggregates the tor_wl30/nym2_wl30/nym5_wl40 re-verification baseline
results (5 seeds each, see scripts/vsc_run_wl_baseline.slurm) the same way
scripts/vsc_aggregate_results.py does for the real per-mode results --
separate script since these variants aren't in the {mode}_seed* glob
pattern the real aggregator uses.

Run ON VSC, from a compute node (results/ lives on scratch, not reliably
visible from the login node):
  srun -M mindwell -A lp_pets -p batch_graniterapids --time=00:05:00 \\
      --mem=8G --exclude=p11c05n1 bash -l -c \\
      'module load Python/3.12.3-GCCcore-13.3.0; \\
       cd $VSC_SCRATCH/wire-adversary-correlator && \\
       python3 scripts/vsc_aggregate_wl_baseline.py'
"""
import json
import glob
import numpy as np

METRICS = [
    "roc_auc", "pr_auc",
    "tpr_at_fpr_1e-02", "tpr_at_fpr_1e-03", "tpr_at_fpr_1e-04", "tpr_at_fpr_1e-05",
]

VARIANTS = [("tor_wl30", "tor"), ("nym2_wl30", "nym2"), ("nym5_wl40", "nym5")]

for variant, mode in VARIANTS:
    files = sorted(glob.glob(f"results/{variant}_seed*/{mode}_eval.json"))
    if not files:
        print(f"{variant}: NO RESULTS FOUND")
        continue
    runs = [json.load(open(f)) for f in files]
    print(f"\n=== {variant} (n={len(runs)} seeds) ===")
    for m in METRICS:
        vals = [r[m] for r in runs if m in r]
        if not vals:
            print(f"  {m:22s}  MISSING")
            continue
        print(f"  {m:22s}  mean={np.mean(vals):.4f}  std={np.std(vals):.4f}  vals={[round(v,4) for v in vals]}")
