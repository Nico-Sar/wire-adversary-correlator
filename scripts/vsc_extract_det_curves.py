"""
scripts/vsc_extract_det_curves.py
==================================
Pulls just {mode, seed, fpr, tpr} out of every results/{mode}_seed*/{mode}_eval.json
(each already saved by model/evaluate.py) into one compact JSON file, small
enough to commit to git -- avoids re-running evaluation or transferring the
full eval.json files (which also carry precision/recall curves and are much
larger) off VSC.

Run ON VSC, from a compute node (results/ lives on the mindwell-visible
scratch, not the login node's mount -- see scripts/vsc_run_array.slurm's
header for why):
  srun -M mindwell -A lp_pets -p batch_graniterapids --time=00:05:00 --pty \\
      bash -l -c 'module load Python/3.12.3-GCCcore-13.3.0; \\
      cd $VSC_SCRATCH/wire-adversary-correlator && \\
      python3 scripts/vsc_extract_det_curves.py'

Writes analysis/det_curves.json (NOT under results/, so it isn't gitignored).
"""
import json
import glob
import numpy as np

MODES = ["vpn", "tor", "nym2", "nym5"]
# Log-spaced FPR grid: raw curves have millions of points (one per unique
# score threshold) -- far more than needed for a plot, and too large to
# commit to git as JSON. DET/ROC curves are monotonic, so interpolating TPR
# onto a fixed log-spaced FPR grid preserves the visual shape (especially
# important across the decades a DET plot's probit scale spans) at a
# fraction of the size.
GRID = np.concatenate([[0.0], np.logspace(-6, 0, 600)])

runs = []
for mode in MODES:
    for f in sorted(glob.glob(f"results/{mode}_seed*/{mode}_eval.json")):
        seed = f.split(f"{mode}_seed")[1].split("/")[0]
        d = json.load(open(f))
        fpr = np.array(d["roc_curve"]["fpr"])
        tpr = np.array(d["roc_curve"]["tpr"])
        tpr_grid = np.interp(GRID, fpr, tpr)
        runs.append({
            "mode": mode,
            "seed": int(seed),
            "fpr": GRID.tolist(),
            "tpr": tpr_grid.tolist(),
            "roc_auc": d["roc_auc"],
            "pr_auc": d["pr_auc"],
        })
        print(f"{mode} seed={seed}: {len(fpr)} raw points -> {len(GRID)} grid points")

import os
os.makedirs("analysis", exist_ok=True)
out_path = "analysis/det_curves.json"
with open(out_path, "w") as fh:
    json.dump(runs, fh)
print(f"Wrote {len(runs)} runs to {out_path}")
