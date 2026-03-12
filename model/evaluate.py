"""
model/evaluate.py
=================
Evaluation of a trained DualCNNCorrelator on the test split.

Outputs:
  - PR-AUC  (primary metric, following ShYSh)
  - ROC-AUC (secondary)
  - Precision-Recall curve plot
  - Confusion matrix at threshold 0.5
  - Per-system comparison table (Nym vs Tor vs VPN vs Baseline)

Usage:
  python evaluate.py --model results/nym_best.pt --dataset data/nym_dataset.npz
"""

import argparse
from pathlib import Path

import torch

from model.cnn import DualCNNCorrelator
from model.dataset import QuartetDataset


def evaluate(model_path: str, dataset_path: str, output_dir: Path):
    """
    Loads a trained model checkpoint and evaluates it on the test split.
    Saves plots and prints a summary metrics table.
    """
    raise NotImplementedError


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model",   required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output",  default="./results")
    args = parser.parse_args()

    evaluate(args.model, args.dataset, Path(args.output))
