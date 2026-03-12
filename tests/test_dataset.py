"""
tests/test_dataset.py
=====================
Unit tests for the PyTorch QuartetDataset.

Validates positive/negative pair generation, hard negative sampling,
and train/val/test split logic.

Run with:
  pytest tests/test_dataset.py -v
"""

import numpy as np
import pytest
import torch

from model.dataset import QuartetDataset


def _make_fake_npz(tmp_path, n_flows=50, n_windows=5, window_len=30, n_urls=5):
    """
    Creates a minimal fake .npz file for testing the dataset loader.
    n_flows flows, distributed across n_urls URLs.
    """
    rng = np.random.default_rng(42)
    shape = (n_flows, n_windows, window_len)

    urls = np.array([f"site{i % n_urls}.com" for i in range(n_flows)])
    modes = np.array(["nym"] * n_flows)
    visit_ids = np.array([f"id{i:04d}" for i in range(n_flows)])

    npz_path = tmp_path / "fake_dataset.npz"
    np.savez_compressed(
        npz_path,
        X_ingress_up=rng.random(shape).astype(np.float32),
        X_ingress_down=rng.random(shape).astype(np.float32),
        X_egress_up=rng.random(shape).astype(np.float32),
        X_egress_down=rng.random(shape).astype(np.float32),
        visit_ids=visit_ids,
        urls=urls,
        modes=modes,
    )
    return str(npz_path)


class TestQuartetDataset:

    def test_loads_without_error(self, tmp_path):
        npz = _make_fake_npz(tmp_path)
        ds = QuartetDataset(npz, split="train")
        assert len(ds) > 0

    def test_item_keys(self, tmp_path):
        """Each item should have the four quartet streams + label."""
        npz = _make_fake_npz(tmp_path)
        ds = QuartetDataset(npz, split="train")
        item = ds[0]
        for key in ["ingress_up", "ingress_down", "egress_up", "egress_down", "label"]:
            assert key in item, f"Missing key: {key}"

    def test_item_tensor_types(self, tmp_path):
        npz = _make_fake_npz(tmp_path)
        ds = QuartetDataset(npz, split="train")
        item = ds[0]
        for key in ["ingress_up", "ingress_down", "egress_up", "egress_down"]:
            assert isinstance(item[key], torch.Tensor)
            assert item[key].dtype == torch.float32

    def test_label_is_binary(self, tmp_path):
        """All labels should be 0 or 1."""
        npz = _make_fake_npz(tmp_path)
        ds = QuartetDataset(npz, split="train")
        labels = set(int(ds[i]["label"].item()) for i in range(len(ds)))
        assert labels.issubset({0, 1})

    def test_neg_pos_ratio(self, tmp_path):
        """Dataset should contain approximately neg_pos_ratio negatives per positive."""
        npz = _make_fake_npz(tmp_path, n_flows=50)
        ds = QuartetDataset(npz, split="train", neg_pos_ratio=10)
        labels = [int(ds[i]["label"].item()) for i in range(len(ds))]
        n_pos = labels.count(1)
        n_neg = labels.count(0)
        # Allow some tolerance around the exact ratio
        assert abs(n_neg / n_pos - 10) < 2, f"Expected ~10:1 ratio, got {n_neg}:{n_pos}"

    def test_splits_are_disjoint(self, tmp_path):
        """Train, val, and test splits must not share flow indices."""
        npz = _make_fake_npz(tmp_path, n_flows=100)
        train_ds = QuartetDataset(npz, split="train")
        val_ds   = QuartetDataset(npz, split="val")
        test_ds  = QuartetDataset(npz, split="test")
        # Each dataset exposes its positive indices
        train_ids = set(train_ds.positive_indices)
        val_ids   = set(val_ds.positive_indices)
        test_ids  = set(test_ds.positive_indices)
        assert train_ids.isdisjoint(val_ids),   "Train/val overlap"
        assert train_ids.isdisjoint(test_ids),  "Train/test overlap"
        assert val_ids.isdisjoint(test_ids),    "Val/test overlap"

    def test_splits_cover_all_flows(self, tmp_path):
        """Union of all split positive indices should cover all flows."""
        npz = _make_fake_npz(tmp_path, n_flows=100)
        all_ids = set()
        for split in ["train", "val", "test"]:
            ds = QuartetDataset(npz, split=split)
            all_ids.update(ds.positive_indices)
        assert len(all_ids) == 100

    def test_reproducibility(self, tmp_path):
        """Same seed → same pair assignments."""
        npz = _make_fake_npz(tmp_path)
        ds1 = QuartetDataset(npz, split="train", seed=42)
        ds2 = QuartetDataset(npz, split="train", seed=42)
        for i in range(min(10, len(ds1))):
            assert ds1[i]["label"] == ds2[i]["label"]
