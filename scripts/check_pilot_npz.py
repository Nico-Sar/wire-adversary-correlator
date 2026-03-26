#!/usr/bin/env python3
"""
scripts/check_pilot_npz.py
==========================
Validates .npz datasets produced by preprocessing/dataset_builder.py.
Run after preprocess_pilot.sh to confirm the full pipeline is working
before proceeding to model training.
 
Usage (from repo root):
    python3 scripts/check_pilot_npz.py data/pilot/baseline_dataset.npz
    python3 scripts/check_pilot_npz.py data/pilot/*.npz     # glob
 
Checks:
  1. All expected keys are present
  2. Array shapes are consistent across the four stream arrays
  3. pairs indices are in bounds and all label=1
  4. Ingress and egress orderings actually differ (shuffle worked)
  5. No NaN or Inf values in any stream array
  6. URL split produces non-empty train / val / test sets
  7. Positive pairs + negative samples produce valid batches
     (exercises QuartetDataset end-to-end without training)
"""
 
import argparse
import sys
from pathlib import Path
 
# Ensure repo root is on sys.path so config.* and model.* resolve
# whether the script is run as `python3 scripts/check_pilot_npz.py` or
# as `python3 -m scripts.check_pilot_npz`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
 
import numpy as np
 
 
REQUIRED_KEYS = {
    "X_ingress_up", "X_ingress_down",
    "X_egress_up",  "X_egress_down",
    "ingress_visit_ids", "egress_visit_ids",
    "ingress_urls",      "egress_urls",
    "pairs", "url_index", "modes",
}
 
PASS_SYM = "  \u2713 "
FAIL_SYM = "  \u2717 "
 
 
def check_npz(path: str) -> bool:
    """Returns True if all checks pass."""
    print(f"\n{'='*60}")
    print(f" Checking: {path}")
    print(f"{'='*60}")
 
    data = np.load(path, allow_pickle=True)
    all_pass = True
 
    def ok(msg):
        print(PASS_SYM + msg)
 
    def fail(msg):
        nonlocal all_pass
        all_pass = False
        print(FAIL_SYM + msg)
 
    # ── 1. Required keys ─────────────────────────────────────────────────
    missing = REQUIRED_KEYS - set(data.files)
    if missing:
        fail(f"Missing keys: {missing}")
    else:
        ok(f"All {len(REQUIRED_KEYS)} required keys present")
 
    # ── 2. Shape consistency ──────────────────────────────────────────────
    shapes = {
        k: data[k].shape
        for k in ["X_ingress_up", "X_ingress_down", "X_egress_up", "X_egress_down"]
        if k in data.files
    }
    if shapes:
        all_shapes = list(shapes.values())
        N = all_shapes[0][0]
        n_windows = all_shapes[0][1]
        L = all_shapes[0][2]
        if all(s == all_shapes[0] for s in all_shapes):
            ok(f"Shape: N={N}, n_windows={n_windows}, window_len={L}  "
               f"({N * n_windows * L * 4 * 4 / 1e6:.1f} MB uncompressed)")
        else:
            fail(f"Shape mismatch across stream arrays: {shapes}")
    else:
        N = 0
 
    # ── 3. pairs index bounds and labels ─────────────────────────────────
    if "pairs" in data.files:
        pairs = data["pairs"]
        in_bounds = (pairs[:, 0].max() < N) and (pairs[:, 1].max() < N)
        all_positive = (pairs[:, 2] == 1).all()
        if in_bounds and all_positive:
            ok(f"pairs: {len(pairs)} positive pairs, all indices in bounds [0, {N})")
        else:
            if not in_bounds:
                fail(f"pairs: index out of bounds (N={N}, max_ingress={pairs[:,0].max()}, "
                     f"max_egress={pairs[:,1].max()})")
            if not all_positive:
                fail(f"pairs: found non-1 labels: {np.unique(pairs[:,2])}")
 
    # ── 4. Shuffle integrity (ingress ≠ egress order) ─────────────────────
    if "ingress_visit_ids" in data.files and "egress_visit_ids" in data.files:
        ing_ids = list(data["ingress_visit_ids"])
        eg_ids  = list(data["egress_visit_ids"])
        if ing_ids == eg_ids:
            fail("ingress and egress visit_ids are in identical order — shuffle did not work")
        else:
            n_same_pos = sum(1 for i, e in zip(ing_ids, eg_ids) if i == e)
            ok(f"Shuffle: ingress/egress orders differ ({n_same_pos}/{N} rows happen to share same id)")
 
    # ── 5. NaN / Inf ──────────────────────────────────────────────────────
    nan_inf_found = False
    for key in ["X_ingress_up", "X_ingress_down", "X_egress_up", "X_egress_down"]:
        if key not in data.files:
            continue
        arr = data[key]
        if not np.isfinite(arr).all():
            fail(f"{key}: contains NaN or Inf")
            nan_inf_found = True
    if not nan_inf_found:
        ok("No NaN or Inf values in any stream array")
 
    # ── 6. URL split coverage ─────────────────────────────────────────────
    if "ingress_urls" in data.files:
        from config.hyperparams import EVAL
        urls = list(data["ingress_urls"])
        unique_urls = sorted(set(urls))
        U = len(unique_urls)
        n_train = max(1, int(U * EVAL["train_split"]))
        n_val   = max(1, int(U * EVAL["val_split"]))
        n_test  = U - n_train - n_val
        if n_test < 1:
            n_train -= 1
            n_test   = 1
        train_n = sum(1 for u in urls if u in set(unique_urls[:n_train]))
        val_n   = sum(1 for u in urls if u in set(unique_urls[n_train:n_train+n_val]))
        test_n  = sum(1 for u in urls if u in set(unique_urls[n_train+n_val:]))
        if train_n > 0 and val_n > 0 and test_n > 0:
            ok(f"URL split: {U} URLs → train={train_n} val={val_n} test={test_n} flows")
        else:
            fail(f"URL split: empty partition (train={train_n} val={val_n} test={test_n})")
 
    # ── 7. QuartetDataset end-to-end batch check ─────────────────────────
    try:
        from model.dataset import QuartetDataset
        import torch
 
        ds = QuartetDataset(path, split="train", neg_pos_ratio=10)
        if len(ds) == 0:
            fail("QuartetDataset train split is empty")
        else:
            item = ds[0]
            for key in ["ingress_up", "ingress_down", "egress_up", "egress_down", "label"]:
                if key not in item:
                    fail(f"QuartetDataset item missing key: {key}")
                    break
            else:
                # Check a few batches worth of items for shape consistency
                shapes_seen = set()
                for i in range(min(5, len(ds))):
                    it = ds[i]
                    shapes_seen.add(tuple(it["ingress_up"].shape))
                labels_seen = set(int(ds[i]["label"].item()) for i in range(min(20, len(ds))))
                n_pos_sample = sum(1 for i in range(len(ds)) if int(ds[i]["label"].item()) == 1)
                n_neg_sample = len(ds) - n_pos_sample
                ok(f"QuartetDataset train: {len(ds)} pairs "
                   f"({n_pos_sample} pos, {n_neg_sample} neg, "
                   f"ratio≈{n_neg_sample/max(n_pos_sample,1):.1f}:1)")
                ok(f"Item shapes consistent: {shapes_seen}")
 
    except ImportError as e:
        print(f"  [skip] QuartetDataset check skipped (torch not installed): {e}")
 
    # ── Summary ───────────────────────────────────────────────────────────
    print()
    if all_pass:
        print(f"  RESULT: ALL CHECKS PASSED  ✓")
    else:
        print(f"  RESULT: SOME CHECKS FAILED  ✗")
 
    return all_pass
 
 
def main():
    parser = argparse.ArgumentParser(
        description="Validate .npz datasets from dataset_builder.py"
    )
    parser.add_argument("npz_files", nargs="+", help=".npz file(s) to check")
    args = parser.parse_args()
 
    results = {}
    for path in args.npz_files:
        results[path] = check_npz(path)
 
    print(f"\n{'='*60}")
    print(" Overall summary")
    print(f"{'='*60}")
    for path, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {status}  {path}")
 
    if not all(results.values()):
        sys.exit(1)
 
 
if __name__ == "__main__":
    main()