"""
analysis/ablation.py
====================
Sweeps KDE hyperparameters and reports PR-AUC for each configuration.
Produces a heatmap of PR-AUC vs (sigma, window_len).

This is the key experiment for justifying the choice of KDE parameters
at the TCP layer vs the ShYSh defaults tuned for Sphinx packets.

Usage:
  python analysis/ablation.py --dataset data/nym_dataset.npz --mode nym
"""

import argparse
import itertools
import numpy as np


# Parameter grids to sweep
SIGMA_GRID      = [0.1, 0.125, 0.25, 0.5, 1.0]   # seconds
WINDOW_LEN_GRID = [15, 30, 60]                      # samples
DURATION_GRID   = [30.0, 60.0]                      # seconds


def run_ablation(dataset_path: str, mode: str, output_dir: str):
    """
    For each (sigma, window_len, duration) combination:
      1. Re-runs preprocessing with those parameters
      2. Trains a fresh correlator
      3. Records test PR-AUC
    Saves results as a .csv and plots a heatmap.
    """
    raise NotImplementedError


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--mode",    required=True)
    parser.add_argument("--output",  default="./results/ablation")
    args = parser.parse_args()

    run_ablation(args.dataset, args.mode, args.output)
