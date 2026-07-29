"""
model/evaluate.py
=================
Evaluation of a trained DualCNNCorrelator on the test split: full
cross-product (real base rate), computed in chunks so the
(ingress_idx, egress_idx, label) index list is never materialized in full
-- only running (score, label) arrays persist. The previous version built
this via QuartetDataset(..., neg_pos_ratio=None), which materializes every
index tuple eagerly; for a 15-16M-pair cross-product that meant an
avoidably large Python object graph and per-pair numpy .copy() calls in
DataLoader. Chunking by ingress index with one batched model call per chunk
avoids both, without changing what's being computed.

Metrics reported (2026-07-27 revision):
  - PR-AUC and ROC-AUC at the real (test-set) base rate -- as before.
  - PR-AUC at ShYSh's three reference base rates (1.9e-4/1.9e-5/1.9e-6),
    via the standard prior-correction reweighting: since ROC (TPR, FPR) is
    base-rate-invariant, precision at an arbitrary hypothetical base rate
    pi is recoverable as precision(pi) = TPR*pi / (TPR*pi + FPR*(1-pi))
    without needing to actually sample enough negatives to realize it.
    This is the metric ShYSh/MixMatch report (Nym-mode comparison).
  - TPR @ FPR = 1e-2/1e-3/1e-4/1e-5 -- the metric DeepCorr/DeepCoFFEA report
    (Tor-mode comparison).
  - Sanity checks: split URL disjointness on the ACTUALLY LOADED data, and
    a base-rate-AWARE near-chance warning -- a no-skill classifier's PR-AUC
    approximately equals the base rate itself, NOT 0.5; a fixed threshold
    on raw PR-AUC would misfire on any rare-positive-class evaluation like
    this one. ROC-AUC (base-rate-invariant) is used for the chance check.

Outputs unchanged in shape for analysis/compare_systems.py compatibility:
  - {mode}_eval.json (pr_auc, roc_auc, precision_recall_curve, roc_curve,
    confusion_at_0.5, base_rate, n_test_pairs, n_positive -- plus the new
    tpr_at_fpr_* / pr_auc_at_*_base_rate fields alongside them)
  - {mode}_eval.png (PR + ROC curve plot)

Usage:
  python -m model.evaluate --model results/vpn_best.pt \\
      --dataset data/vpn_dataset.npz --mode vpn
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch

from config.hyperparams import MODEL, EVAL
from model.cnn import DualCNNCorrelator

TARGET_BASE_RATES = [1.9e-4, 1.9e-5, 1.9e-6]
TARGET_FPRS = [1e-2, 1e-3, 1e-4, 1e-5]
CHUNK_ING = 40  # ingress rows per outer chunk -- bounds peak memory
# Reduced from 100 (2026-07-29): the per-mode window_len retune (tor/nym2:
# 30->10 samples) roughly triples windows-per-flow (32s duration: ~20
# windows at wl=30 vs ~63 at wl=10), and this chunked cross-product's peak
# tensor memory scales directly with window count -- caused an OOM kill on
# VSC (32GB alloc) that hadn't happened before the retune. 40 gives
# comparable effective chunk memory to the old CHUNK_ING=100/wl=30
# combination (100/3.15~=32, rounded up for a little margin).


def _get_split_indices(data, split):
    pairs_arr = data["pairs"]
    flow_urls = list(data["ingress_urls"])
    all_pos = [(int(r[0]), int(r[1])) for r in pairs_arr]

    unique_urls = sorted(set(flow_urls))
    U = len(unique_urls)
    n_train = int(U * EVAL["train_split"])
    n_val = int(U * EVAL["val_split"])
    if U >= 3:
        n_val = max(1, n_val)
        if U - n_train - n_val < 1:
            n_train -= 1
    train_urls = set(unique_urls[:n_train])
    val_urls = set(unique_urls[n_train:n_train + n_val])
    test_urls = set(unique_urls[n_train + n_val:])

    split_url_set = {"train": train_urls, "val": val_urls, "test": test_urls}[split]
    split_pos = [(i, e) for i, e in all_pos if flow_urls[i] in split_url_set]
    return split_pos, train_urls, val_urls, test_urls, flow_urls


def _check_split_disjointness(data):
    """Sanity check: verify train/val/test URL sets are disjoint in the
    ACTUALLY LOADED dataset, not just assumed from the splitting logic."""
    _, train_urls, val_urls, test_urls, _ = _get_split_indices(data, "test")
    overlaps = {
        "train&val": train_urls & val_urls,
        "train&test": train_urls & test_urls,
        "val&test": val_urls & test_urls,
    }
    for name, ov in overlaps.items():
        if ov:
            raise RuntimeError(f"URL LEAKAGE ({name}): {len(ov)} URLs overlap: {list(ov)[:5]}")
    print(f"Split disjointness OK: train={len(train_urls)} val={len(val_urls)} test={len(test_urls)} URLs, "
          f"no overlap.")


def _reweighted_pr_auc(tpr, fpr, target_rate):
    """precision(pi) = TPR*pi / (TPR*pi + FPR*(1-pi)); tpr/fpr from
    sklearn.roc_curve (base-rate-invariant, monotonic in recall)."""
    denom = tpr * target_rate + fpr * (1 - target_rate)
    precision = np.where(denom > 0, (tpr * target_rate) / np.maximum(denom, 1e-300), 1.0)
    recall = tpr
    order = np.argsort(recall)
    from sklearn.metrics import auc
    return float(auc(recall[order], precision[order]))


def evaluate(model_path: str, dataset_path: str, mode: str, output_dir: Path,
             num_threads: int | None = None) -> dict:
    """
    Loads a trained model checkpoint and evaluates it on the full test-set
    cross-product (real base rate). Saves plots and a metrics JSON to
    output_dir, and returns the metrics dict.
    """
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type == "cpu" and num_threads:
        # Explicit call, matching what was kill/resume-tested on amazone --
        # see the same note in model/train.py.
        torch.set_num_threads(num_threads)
    print(f"Device: {device}  threads: {torch.get_num_threads() if device.type == 'cpu' else 'n/a'}")

    data = np.load(dataset_path, allow_pickle=True)
    _check_split_disjointness(data)

    split_pos, _, _, test_urls, flow_urls = _get_split_indices(data, "test")
    positive_set = set(split_pos)
    ing_indices = sorted({i for i, _ in split_pos})
    eg_indices = sorted({e for _, e in split_pos})
    n_cross = len(ing_indices) * len(eg_indices)
    print(f"Test split: {len(test_urls)} URLs, {len(ing_indices)} ingress flows, "
          f"{len(eg_indices)} egress flows -> {n_cross:,} cross-product pairs "
          f"(streamed in chunks, never materialized as an index list)")

    model = DualCNNCorrelator().to(device)
    model.load_state_dict(torch.load(model_path, map_location=device))
    model.eval()

    eg_up_t = torch.from_numpy(data["X_egress_up"][eg_indices].copy()).to(device)
    eg_dn_t = torch.from_numpy(data["X_egress_down"][eg_indices].copy()).to(device)
    n_eg = len(eg_indices)
    W = eg_up_t.shape[1]
    L = eg_up_t.shape[2]

    all_scores = np.empty(n_cross, dtype=np.float32)
    all_labels = np.empty(n_cross, dtype=np.uint8)
    write_ptr = 0

    t0 = time.perf_counter()
    with torch.no_grad():
        for c0 in range(0, len(ing_indices), CHUNK_ING):
            chunk_ing = ing_indices[c0:c0 + CHUNK_ING]
            B_ing = len(chunk_ing)
            i_up = torch.from_numpy(data["X_ingress_up"][chunk_ing].copy()).to(device)
            i_dn = torch.from_numpy(data["X_ingress_down"][chunk_ing].copy()).to(device)

            i_up_b = i_up.unsqueeze(1).expand(B_ing, n_eg, W, L).reshape(B_ing * n_eg * W, L)
            i_dn_b = i_dn.unsqueeze(1).expand(B_ing, n_eg, W, L).reshape(B_ing * n_eg * W, L)
            e_up_b = eg_up_t.unsqueeze(0).expand(B_ing, n_eg, W, L).reshape(B_ing * n_eg * W, L)
            e_dn_b = eg_dn_t.unsqueeze(0).expand(B_ing, n_eg, W, L).reshape(B_ing * n_eg * W, L)

            logits = model(i_up_b, i_dn_b, e_up_b, e_dn_b)
            scores = torch.sigmoid(logits).view(B_ing, n_eg, W).mean(dim=2)
            scores_np = scores.cpu().numpy().astype(np.float32)

            for bi, ing_idx in enumerate(chunk_ing):
                row_labels = np.fromiter(
                    ((1 if (ing_idx, eg_idx) in positive_set else 0) for eg_idx in eg_indices),
                    dtype=np.uint8, count=n_eg)
                all_scores[write_ptr:write_ptr + n_eg] = scores_np[bi]
                all_labels[write_ptr:write_ptr + n_eg] = row_labels
                write_ptr += n_eg

            if (c0 // CHUNK_ING) % 10 == 0:
                elapsed = time.perf_counter() - t0
                print(f"  {write_ptr:,}/{n_cross:,} pairs ({write_ptr/n_cross:.1%})  elapsed={elapsed:.0f}s",
                      flush=True)

    elapsed = time.perf_counter() - t0
    print(f"Scored full cross-product in {elapsed:.0f}s ({elapsed/60:.1f} min)")

    scores, labels = all_scores, all_labels
    n_pos = int(labels.sum())
    base_rate = n_pos / len(labels) if len(labels) else float("nan")
    print(f"Test pairs: {len(labels)}  ({n_pos} pos, base_rate={base_rate:.2e})")

    from sklearn.metrics import (average_precision_score, roc_auc_score,
                                  confusion_matrix, precision_recall_curve, roc_curve)

    pr_auc = float(average_precision_score(labels, scores))
    roc_auc = float(roc_auc_score(labels, scores)) if 0 < n_pos < len(labels) else float("nan")
    preds = (scores >= 0.5).astype(int)
    tn, fp, fn, tp = confusion_matrix(labels, preds, labels=[0, 1]).ravel()
    precision, recall, _ = precision_recall_curve(labels, scores)
    fpr, tpr, _ = roc_curve(labels, scores)

    tpr_at_fpr = {f"tpr_at_fpr_{f:.0e}": float(np.interp(f, fpr, tpr)) for f in TARGET_FPRS}
    pr_auc_at_rate = {f"pr_auc_at_{r:.1e}": _reweighted_pr_auc(tpr, fpr, r) for r in TARGET_BASE_RATES}

    print(f"PR-AUC:  {pr_auc:.4f}")
    print(f"ROC-AUC: {roc_auc:.4f}")
    print(f"Confusion @0.5: tp={tp} fp={fp} tn={tn} fn={fn}")

    # Base-rate-aware sanity band: a no-skill classifier's PR-AUC approx
    # equals the base rate itself, NOT 0.5 -- a fixed threshold is wrong
    # for a base-rate-sensitive metric like this. Use ROC-AUC (base-rate-
    # invariant) for the "near chance" check instead.
    if not np.isnan(roc_auc) and roc_auc < 0.6:
        print(f"WARNING: ROC-AUC ({roc_auc:.4f}) is near chance (0.5) -- "
              f"check for a pipeline bug (mismatched labels, untrained model).")
    elif pr_auc < 3 * base_rate:
        print(f"WARNING: PR-AUC ({pr_auc:.4f}) is within 3x the base rate "
              f"({base_rate:.2e}) despite ROC-AUC={roc_auc:.4f} -- possible early-recall degradation.")
    else:
        print(f"PR-AUC ({pr_auc:.4f}) is {pr_auc/base_rate:.0f}x the base rate "
              f"({base_rate:.2e}) -- consistent with a real signal, not chance.")
    if not np.isnan(roc_auc) and roc_auc > 0.9999:
        print(f"WARNING: ROC-AUC ({roc_auc:.4f}) is suspiciously perfect -- check for train/test leakage.")

    output_dir.mkdir(parents=True, exist_ok=True)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    axes[0].plot(recall, precision)
    axes[0].set_xlabel("Recall")
    axes[0].set_ylabel("Precision")
    axes[0].set_title(f"{mode.upper()} PR curve (AUC={pr_auc:.3f})")
    axes[1].plot(fpr, tpr)
    axes[1].plot([0, 1], [0, 1], linestyle="--", color="gray")
    axes[1].set_xlabel("FPR")
    axes[1].set_ylabel("TPR")
    axes[1].set_title(f"{mode.upper()} ROC curve (AUC={roc_auc:.3f})")
    fig.tight_layout()
    plot_path = output_dir / f"{mode}_eval.png"
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)

    metrics = {
        "mode": mode,
        "n_test_pairs": int(len(labels)),
        "n_positive": n_pos,
        "base_rate": base_rate,
        "pr_auc": pr_auc,
        "roc_auc": roc_auc,
        **tpr_at_fpr,
        **pr_auc_at_rate,
        "confusion_at_0.5": {"tp": int(tp), "fp": int(fp), "tn": int(tn), "fn": int(fn)},
        "precision_recall_curve": {"precision": precision.tolist(), "recall": recall.tolist()},
        "roc_curve": {"fpr": fpr.tolist(), "tpr": tpr.tolist()},
    }
    json_path = output_dir / f"{mode}_eval.json"
    json_path.write_text(json.dumps(metrics, indent=2))
    print(f"Saved: {json_path}")
    print(f"Saved: {plot_path}")
    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Evaluate a trained DualCNNCorrelator on the full test-set cross-product"
    )
    parser.add_argument("--model",   required=True, help="Path to {mode}_best.pt checkpoint")
    parser.add_argument("--dataset", required=True, help="Path to .npz produced by dataset_builder.py")
    parser.add_argument("--mode",    required=True, choices=["tor", "vpn", "nym5", "nym2"])
    parser.add_argument("--output",  default="./results")
    parser.add_argument("--threads", type=int, default=None,
                        help="Explicit torch.set_num_threads() on CPU -- see model/train.py "
                             "for why this matters more than OMP_NUM_THREADS alone.")
    args = parser.parse_args()

    evaluate(args.model, args.dataset, args.mode, Path(args.output), num_threads=args.threads)
