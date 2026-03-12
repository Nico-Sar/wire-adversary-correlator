"""
model/train.py
==============
Training loop for the DualCNNCorrelator.

Primary metric: PR-AUC (Precision-Recall AUC), following ShYSh.
PR-AUC is preferred over ROC-AUC for imbalanced datasets where
the positive class (paired flows) is rare.

Usage:
  python train.py --dataset ../data/nym_dataset.npz --mode nym
"""

import argparse
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from model.cnn import DualCNNCorrelator
from model.dataset import QuartetDataset
from config.hyperparams import MODEL, EVAL


def train(dataset_path: str, mode: str, output_dir: Path):
    """
    Full training loop:
      - Loads train/val splits from QuartetDataset
      - Trains DualCNNCorrelator with binary cross-entropy loss
      - Validates PR-AUC after each epoch
      - Saves best model checkpoint by val PR-AUC
    """
    raise NotImplementedError


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--mode",    required=True, choices=["nym", "tor", "vpn", "baseline"])
    parser.add_argument("--output",  default="./results")
    args = parser.parse_args()

    train(args.dataset, args.mode, Path(args.output))
