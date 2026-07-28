import json, glob
import numpy as np

MODES = ["vpn", "tor", "nym2", "nym5"]
METRICS = [
    "roc_auc", "pr_auc",
    "tpr_at_fpr_1e-02", "tpr_at_fpr_1e-03", "tpr_at_fpr_1e-04", "tpr_at_fpr_1e-05",
    "pr_auc_at_1.9e-04", "pr_auc_at_1.9e-05", "pr_auc_at_1.9e-06",
]

for mode in MODES:
    files = sorted(glob.glob(f"results/{mode}_seed*/{mode}_eval.json"))
    if not files:
        print(f"{mode}: NO RESULTS FOUND")
        continue
    runs = [json.load(open(f)) for f in files]
    print(f"\n=== {mode}  (n={len(runs)} seeds) ===")
    for m in METRICS:
        vals = [r[m] for r in runs if m in r]
        if not vals:
            print(f"  {m:22s}  MISSING")
            continue
        print(f"  {m:22s}  mean={np.mean(vals):.4f}  std={np.std(vals):.4f}  vals={[round(v,4) for v in vals]}")
