"""
model/evaluate.py
=================
Evaluation of a trained DualCNNCorrelator on the test split.

Uses the full cross-product test set (QuartetDataset(..., neg_pos_ratio=None))
rather than train.py's fixed 10:1 sampling ratio, so PR-AUC reflects the real
deployment base rate (~1/U_eg, U_eg = distinct egress flows in the split)
instead of an artificially balanced one — see dataset.py's neg_pos_ratio
docstring for why this distinction matters for a base-rate-sensitive metric.

Outputs:
  - PR-AUC  (primary metric, following ShYSh)
  - ROC-AUC (secondary)
  - Precision-Recall curve + ROC curve plot (output_dir/{mode}_eval.png)
  - Confusion matrix at threshold 0.5
  - {mode}_eval.json — consumed by analysis/compare_systems.py for the
    cross-system comparison figure

Usage:
  python -m model.evaluate --model results/vpn_best.pt \\
      --dataset data/vpn_dataset.npz --mode vpn
"""

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader

from config.hyperparams import MODEL
from model.cnn import DualCNNCorrelator
from model.dataset import QuartetDataset
from model.train import _forward_batch, compute_pr_auc


def compute_roc_auc(scores: np.ndarray, labels: np.ndarray) -> float:
    from sklearn.metrics import roc_auc_score
    if labels.sum() == 0 or labels.sum() == len(labels):
        return float("nan")
    return float(roc_auc_score(labels, scores))


def evaluate(model_path: str, dataset_path: str, mode: str, output_dir: Path) -> dict:
    """
    Loads a trained model checkpoint and evaluates it on the test split.
    Saves plots and a metrics JSON to output_dir, and returns the metrics dict.
    """
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    # neg_pos_ratio=None -> full cross-product, real base rate (see module docstring)
    test_ds = QuartetDataset(dataset_path, split="test", neg_pos_ratio=None)
    n_pos = sum(1 for i in range(len(test_ds)) if int(test_ds[i]["label"].item()) == 1)
    base_rate = n_pos / len(test_ds) if len(test_ds) else float("nan")
    print(f"Test pairs: {len(test_ds)}  ({n_pos} pos, base_rate={base_rate:.2e})")

    test_loader = DataLoader(test_ds, batch_size=MODEL["batch_size"],
                              shuffle=False, num_workers=0)

    model = DualCNNCorrelator().to(device)
    model.load_state_dict(torch.load(model_path, map_location=device))
    model.eval()

    all_scores = []
    all_labels = []
    with torch.no_grad():
        for batch in test_loader:
            scores, labels = _forward_batch(model, batch, device)
            all_scores.append(torch.sigmoid(scores).cpu().numpy())
            all_labels.append(labels.cpu().numpy())
    scores = np.concatenate(all_scores)
    labels = np.concatenate(all_labels)

    pr_auc = compute_pr_auc(scores, labels)
    roc_auc = compute_roc_auc(scores, labels)

    from sklearn.metrics import confusion_matrix, precision_recall_curve, roc_curve
    preds = (scores >= 0.5).astype(int)
    tn, fp, fn, tp = confusion_matrix(labels, preds, labels=[0, 1]).ravel()
    precision, recall, _ = precision_recall_curve(labels, scores)
    fpr, tpr, _ = roc_curve(labels, scores)

    print(f"PR-AUC:  {pr_auc:.4f}")
    print(f"ROC-AUC: {roc_auc:.4f}")
    print(f"Confusion @0.5: tp={tp} fp={fp} tn={tn} fn={fn}")

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
        "n_test_pairs": len(test_ds),
        "n_positive": n_pos,
        "base_rate": base_rate,
        "pr_auc": pr_auc,
        "roc_auc": roc_auc,
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
        description="Evaluate a trained DualCNNCorrelator on the test split"
    )
    parser.add_argument("--model",   required=True, help="Path to {mode}_best.pt checkpoint")
    parser.add_argument("--dataset", required=True, help="Path to .npz produced by dataset_builder.py")
    parser.add_argument("--mode",    required=True, choices=["tor", "vpn", "nym5", "nym2"])
    parser.add_argument("--output",  default="./results")
    args = parser.parse_args()

    evaluate(args.model, args.dataset, args.mode, Path(args.output))
