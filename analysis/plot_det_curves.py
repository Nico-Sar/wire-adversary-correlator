"""
analysis/plot_det_curves.py
============================
DET (detection error tradeoff) curves from the 20-run VSC array job
(4 modes x 5 seeds). Reads the compact fpr/tpr grid extracted by
scripts/vsc_extract_det_curves.py (analysis/det_curves.json) -- not the raw
per-run eval.json files.

Produces two figure sets:
  - figures/det_curves/cross_mode.pdf: one curve per mode, each mode's
    REPRESENTATIVE seed only (the seed whose ROC-AUC is closest to that
    mode's 5-seed mean -- a fixed, reproducible selection rule stated in the
    caption, not a cherry-picked run).
  - figures/det_curves/within_mode_{mode}.pdf, one per mode: all 5 seeds for
    that mode overlaid, to show cross-seed spread directly (the same
    variability summarized by the std.\ columns in Table~tab:permode-tprfpr,
    but visible as curve shape rather than a single-point std.).

DET curves plot FNR (=1-TPR) against FPR on a normal-deviate (probit) axis
scale, on which score distributions that are approximately Gaussian trace
straight lines and different systems' relative performance across operating
points is visible at a glance (Martin et al., 1997).

Usage:
  python analysis/plot_det_curves.py
"""

import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from sklearn.metrics import DetCurveDisplay

MODE_ORDER = ["tor", "vpn", "nym2", "nym5"]
MODE_LABELS = {"vpn": "VPN", "tor": "Tor", "nym5": "Nym (5-hop)", "nym2": "Nym (2-hop)"}
# dataviz skill categorical palette, first 4 slots (validated CVD-safe, adjacent
# and all-pairs, for this exact series count) -- fixed order, one hue per mode,
# never reassigned across figures.
MODE_COLOR = {"tor": "#2a78d6", "vpn": "#eb6834", "nym2": "#1baf7a", "nym5": "#eda100"}
MODE_LINESTYLE = {"tor": "-", "vpn": "--", "nym2": "-.", "nym5": ":"}

EPS = 1e-6  # clip away from exact 0/1 so the probit axis transform stays finite


def _clean(fpr, tpr):
    """Drop the degenerate (0,0)/(1,1) corners and clip near 0/1, which
    otherwise map to +/-inf on the probit-scaled DET axes."""
    fpr = np.asarray(fpr)
    fnr = 1.0 - np.asarray(tpr)
    mask = (fpr > 0) & (fpr < 1)
    fpr, fnr = fpr[mask], fnr[mask]
    fpr = np.clip(fpr, EPS, 1 - EPS)
    fnr = np.clip(fnr, EPS, 1 - EPS)
    return fpr, fnr


def load_runs(path="analysis/det_curves.json"):
    with open(path) as f:
        runs = json.load(f)
    by_mode = {m: [] for m in MODE_ORDER}
    for r in runs:
        by_mode[r["mode"]].append(r)
    for m in by_mode:
        by_mode[m].sort(key=lambda r: r["seed"])
    return by_mode


def representative_seed(runs_for_mode):
    """Seed whose PR-AUC is closest to the mode's 5-seed mean -- a fixed,
    stated rule so the "one representative run per mode" plot is
    reproducible and not a cherry-picked best case. PR-AUC, not ROC-AUC:
    Section~sec:eval-protocol's own argument for preferring PR-AUC is that
    ROC-AUC is insensitive to the class imbalance this thesis is about, which
    makes it a poor criterion for "typicality" too -- an earlier version of
    this function used ROC-AUC and picked VPN's seed 3, the one seed flagged
    elsewhere (Section~sec:permode) as an outlier on every imbalance-sensitive
    metric (TPR@1e-3, PR-AUC); ROC-AUC couldn't see it because ROC-AUC is
    exactly the metric that's blind to that kind of variability."""
    aucs = np.array([r["pr_auc"] for r in runs_for_mode])
    mean_auc = aucs.mean()
    idx = int(np.argmin(np.abs(aucs - mean_auc)))
    return runs_for_mode[idx]


def plot_cross_mode(by_mode, output_path):
    fig, ax = plt.subplots(figsize=(6, 5.5))
    for mode in MODE_ORDER:
        run = representative_seed(by_mode[mode])
        fpr, fnr = _clean(run["fpr"], run["tpr"])
        DetCurveDisplay(fpr=fpr, fnr=fnr).plot(
            ax=ax, name=f"{MODE_LABELS[mode]} (seed {run['seed']}, PR-AUC={run['pr_auc']:.3f}, ROC-AUC={run['roc_auc']:.3f})",
            color=MODE_COLOR[mode], linestyle=MODE_LINESTYLE[mode], linewidth=1.6,
        )
    ax.set_title("DET curves: one representative run per mode")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, which="both", linewidth=0.4, alpha=0.4)
    fig.tight_layout()
    fig.savefig(f"{output_path}/cross_mode.pdf")
    fig.savefig(f"{output_path}/cross_mode.png", dpi=150)
    plt.close(fig)
    print(f"Saved: {output_path}/cross_mode.{{pdf,png}}")


SEED_LINESTYLE = ["-", "--", "-.", ":", (0, (3, 1, 1, 1, 1, 1))]  # 5 distinct styles


def plot_within_mode(by_mode, output_path):
    for mode in MODE_ORDER:
        fig, ax = plt.subplots(figsize=(5.5, 5))
        color = MODE_COLOR[mode]
        for i, run in enumerate(by_mode[mode]):
            fpr, fnr = _clean(run["fpr"], run["tpr"])
            DetCurveDisplay(fpr=fpr, fnr=fnr).plot(
                ax=ax, name=f"seed {run['seed']} (PR-AUC={run['pr_auc']:.3f}, ROC-AUC={run['roc_auc']:.3f})",
                color=color, linewidth=1.3, alpha=0.85, linestyle=SEED_LINESTYLE[i],
            )
        ax.set_title(f"DET curves, all 5 seeds: {MODE_LABELS[mode]}")
        ax.legend(loc="upper right", fontsize=8)
        ax.grid(True, which="both", linewidth=0.4, alpha=0.4)
        fig.tight_layout()
        fig.savefig(f"{output_path}/within_mode_{mode}.pdf")
        fig.savefig(f"{output_path}/within_mode_{mode}.png", dpi=150)
        plt.close(fig)
        print(f"Saved: {output_path}/within_mode_{mode}.{{pdf,png}}")


if __name__ == "__main__":
    import os
    output_path = "figures/det_curves"
    os.makedirs(output_path, exist_ok=True)
    by_mode = load_runs()
    plot_cross_mode(by_mode, output_path)
    plot_within_mode(by_mode, output_path)
