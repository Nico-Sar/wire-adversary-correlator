"""
model/dataset.py
================
PyTorch Dataset for Quartet flow pairs.

Positive pairs:  (ingress_windows, egress_windows) from the same visit → label 1
Negative pairs:  ingress from visit A, egress from visit B            → label 0

Negative sampling follows ShYSh: 10 negatives per positive, including
"hard negatives" drawn from the same URL to prevent the model from
learning URL fingerprints instead of flow shapes.
"""

import numpy as np
import torch
from torch.utils.data import Dataset


class QuartetDataset(Dataset):
    """
    Loads a .npz quartet archive and generates positive + negative pairs.

    Args:
        npz_path:      path to .npz produced by dataset_builder.py
        neg_pos_ratio: number of negative pairs per positive (default 10)
        hard_neg_frac: fraction of negatives drawn from same URL (default 0.5)
        split:         'train', 'val', or 'test'
        seed:          random seed for reproducibility
    """

    def __init__(self, npz_path: str, neg_pos_ratio: int = 10,
                 hard_neg_frac: float = 0.5,
                 split: str = "train", seed: int = 42):
        raise NotImplementedError

    def __len__(self) -> int:
        raise NotImplementedError

    def __getitem__(self, idx: int) -> dict:
        """
        Returns dict with keys:
            ingress_up, ingress_down, egress_up, egress_down  (torch.float32, window_len)
            label  (torch.float32, scalar: 0 or 1)
        """
        raise NotImplementedError
