"""
analysis/compare_systems.py
============================
Generates the main thesis comparison figure:
PR-AUC of the correlator across VPN, Tor, Nym5 (5-hop), and Nym2 (2-hop).

One classifier is trained per mode (see model/train.py --mode); this script
consumes each mode's model/evaluate.py JSON output and overlays them so the
core thesis result -- how much correlation signal survives each anonymity
system at the TCP/IP wire layer -- is comparable on one figure.

Usage:
  python analysis/compare_systems.py \\
      --vpn  results/vpn_eval.json \\
      --tor  results/tor_eval.json \\
      --nym5 results/nym5_eval.json \\
      --nym2 results/nym2_eval.json \\
      --output figures/system_comparison
"""

import argparse
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Fixed order/labels so every figure in the thesis is consistent regardless
# of CLI argument order.
MODE_ORDER = ["vpn", "tor", "nym5", "nym2"]
MODE_LABELS = {"vpn": "VPN", "tor": "Tor", "nym5": "Nym (5-hop)", "nym2": "Nym (2-hop)"}


def _load(eval_paths: dict[str, str]) -> dict[str, dict]:
    loaded = {}
    for mode, path in eval_paths.items():
        if path is None:
            continue
        with open(path) as f:
            loaded[mode] = json.load(f)
    return loaded


def plot_pr_curves(eval_paths: dict[str, str], output_path: str | None = None):
    """
    Overlays Precision-Recall curves for all available systems on one plot.
    Each curve labelled with its PR-AUC score.

    eval_paths: dict mapping mode name -> path to evaluate.py JSON output
                (missing/None entries are skipped, so partial comparisons work)
    """
    results = _load(eval_paths)
    fig, ax = plt.subplots(figsize=(6, 5))
    for mode in MODE_ORDER:
        if mode not in results:
            continue
        r = results[mode]
        curve = r["precision_recall_curve"]
        ax.plot(curve["recall"], curve["precision"],
                label=f"{MODE_LABELS[mode]} (AUC={r['pr_auc']:.3f})")
    ax.set_xlabel("Recall")
    ax.set_ylabel("Precision")
    ax.set_title("Flow correlation: Precision-Recall by anonymity system")
    ax.legend()
    fig.tight_layout()
    if output_path:
        fig.savefig(f"{output_path}_pr_curves.png", dpi=150)
        print(f"Saved: {output_path}_pr_curves.png")
    return fig


def plot_prauc_bar(eval_paths: dict[str, str], output_path: str | None = None):
    """
    Bar chart of PR-AUC per system -- the single clearest summary figure
    for the thesis results chapter.
    """
    results = _load(eval_paths)
    modes = [m for m in MODE_ORDER if m in results]
    values = [results[m]["pr_auc"] for m in modes]
    labels = [MODE_LABELS[m] for m in modes]

    fig, ax = plt.subplots(figsize=(6, 4.5))
    bars = ax.bar(labels, values)
    for bar, v in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, v, f"{v:.3f}",
                ha="center", va="bottom")
    ax.set_ylabel("PR-AUC")
    ax.set_ylim(0, 1.0)
    ax.set_title("Correlator PR-AUC by anonymity system")
    fig.tight_layout()
    if output_path:
        fig.savefig(f"{output_path}_prauc_bar.png", dpi=150)
        print(f"Saved: {output_path}_prauc_bar.png")
    return fig


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--vpn",      default=None)
    parser.add_argument("--tor",      default=None)
    parser.add_argument("--nym5",     default=None)
    parser.add_argument("--nym2",     default=None)
    parser.add_argument("--output",   default=None,
                         help="Path prefix for output PNGs (e.g. figures/system_comparison)")
    args = parser.parse_args()

    paths = {
        "vpn":  args.vpn,
        "tor":  args.tor,
        "nym5": args.nym5,
        "nym2": args.nym2,
    }
    if not any(paths.values()):
        parser.error("at least one of --vpn/--tor/--nym5/--nym2 must be given")

    plot_pr_curves(paths, args.output)
    plot_prauc_bar(paths, args.output)
