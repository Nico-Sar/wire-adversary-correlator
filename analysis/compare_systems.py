"""
analysis/compare_systems.py
============================
Generates the main thesis comparison figure:
PR-AUC of the correlator across Nym, Tor, VPN, and Baseline.

This is the core result of the thesis — quantifying how much correlation
signal survives each anonymity system at the TCP/IP wire layer.

Usage:
  python analysis/compare_systems.py \
      --nym      results/nym_eval.json \
      --tor      results/tor_eval.json \
      --vpn      results/vpn_eval.json \
      --baseline results/baseline_eval.json
"""

import argparse
import json
import matplotlib.pyplot as plt


def plot_pr_curves(eval_paths: dict[str, str], output_path: str | None = None):
    """
    Overlays Precision-Recall curves for all four systems on one plot.
    Each curve labelled with its PR-AUC score.

    eval_paths: dict mapping mode name → path to evaluate.py JSON output
    """
    raise NotImplementedError


def plot_prauc_bar(eval_paths: dict[str, str], output_path: str | None = None):
    """
    Bar chart of PR-AUC per system — the single clearest summary figure
    for the thesis results chapter.
    """
    raise NotImplementedError


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--nym",      required=True)
    parser.add_argument("--tor",      required=True)
    parser.add_argument("--vpn",      required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--output",   default=None)
    args = parser.parse_args()

    paths = {
        "Nym":      args.nym,
        "Tor":      args.tor,
        "VPN":      args.vpn,
        "Baseline": args.baseline,
    }
    plot_pr_curves(paths, args.output)
    plot_prauc_bar(paths, args.output)
