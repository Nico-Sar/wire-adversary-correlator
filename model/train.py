"""
model/train.py
==============
Training loop for the DualCNNCorrelator.

Primary metric: PR-AUC (Precision-Recall AUC), following ShYSh.
PR-AUC is preferred over ROC-AUC for imbalanced datasets where
the positive class (paired flows) is rare.

Window reshaping
----------------
QuartetDataset returns tensors of shape (n_windows, window_len) per stream.
DataLoader batches these to (B, W, L). Before the CNN forward pass we reshape
to (B*W, L) so each window is processed independently, then aggregate the
per-window scores back to per-flow scores via mean pooling: (B*W,1) → (B,W) → mean → (B,).
This matches ShYSh's window-level scoring followed by flow-level aggregation.

Usage:
  # Full training run:
  python -m model.train --dataset data/baseline_dataset.npz --mode baseline

  # Quick sanity check (first 10 URLs, 1 epoch):
  python -m model.train --dataset data/baseline_dataset.npz --mode baseline \\
      --max_urls 10 --epochs 1
"""

import argparse
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Subset

from config.hyperparams import MODEL, EVAL
from model.cnn import DualCNNCorrelator
from model.dataset import QuartetDataset


# ── Helpers ───────────────────────────────────────────────────────────────────

def _url_subset(dataset: QuartetDataset, max_urls: int) -> Subset:
    """Return a Subset of dataset limited to the first max_urls URLs (alphabetical)."""
    kept_urls = set(sorted(set(dataset.pair_urls))[:max_urls])
    indices   = [i for i, url in enumerate(dataset.pair_urls) if url in kept_urls]
    return Subset(dataset, indices)


def _forward_batch(model: DualCNNCorrelator,
                   batch: dict,
                   device: torch.device) -> tuple[torch.Tensor, torch.Tensor]:
    """
    Reshape (B, W, L) windows to (B*W, L), run CNN, aggregate back to (B,).
    Returns (flow_scores, labels) both on device.
    """
    B = batch["ingress_up"].shape[0]
    W = batch["ingress_up"].shape[1]
    L = batch["ingress_up"].shape[2]

    i_up   = batch["ingress_up"].view(B * W, L).to(device)
    i_down = batch["ingress_down"].view(B * W, L).to(device)
    e_up   = batch["egress_up"].view(B * W, L).to(device)
    e_down = batch["egress_down"].view(B * W, L).to(device)
    labels = batch["label"].to(device)

    scores = model(i_up, i_down, e_up, e_down)  # (B*W, 1)
    scores = scores.view(B, W).mean(dim=1)        # (B,)  flow-level score
    return scores, labels


def run_epoch(model:     DualCNNCorrelator,
              loader:    DataLoader,
              criterion: nn.Module,
              device:    torch.device,
              optimizer: torch.optim.Optimizer | None = None,
              label:     str = "train") -> tuple[float, np.ndarray, np.ndarray]:
    """
    Run one epoch (train if optimizer supplied, else eval).
    Returns (avg_loss, all_scores_np, all_labels_np).
    Raises ValueError on NaN/Inf loss.
    """
    training = optimizer is not None
    model.train(training)

    total_loss  = 0.0
    all_scores  = []
    all_labels  = []

    with torch.set_grad_enabled(training):
        for batch_idx, batch in enumerate(loader):
            scores, labels = _forward_batch(model, batch, device)
            loss = criterion(scores, labels)

            if not torch.isfinite(loss):
                raise ValueError(
                    f"[{label}] Non-finite loss at batch {batch_idx}: {loss.item()}"
                )

            if training:
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

            total_loss += loss.item()
            # sigmoid converts logits to [0,1] for PR-AUC — not needed by the loss
            all_scores.append(torch.sigmoid(scores).detach().cpu().numpy())
            all_labels.append(labels.detach().cpu().numpy())

    avg_loss = total_loss / max(len(loader), 1)
    return avg_loss, np.concatenate(all_scores), np.concatenate(all_labels)


def compute_pr_auc(scores: np.ndarray, labels: np.ndarray) -> float:
    from sklearn.metrics import average_precision_score
    if labels.sum() == 0:
        return float("nan")
    return float(average_precision_score(labels, scores))


# ── Main training loop ────────────────────────────────────────────────────────

def train(dataset_path: str,
          mode:         str,
          output_dir:   Path,
          max_urls:     int | None = None,
          n_epochs:     int = MODEL["epochs"]) -> None:
    """
    Full training loop with train/val split.
    Saves best checkpoint by val PR-AUC to output_dir/{mode}_best.pt.
    """
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    # ── Datasets ──────────────────────────────────────────────────────────
    train_ds_full = QuartetDataset(dataset_path, split="train",
                                   neg_pos_ratio=MODEL["neg_pos_ratio"])
    val_ds        = QuartetDataset(dataset_path, split="val",
                                   neg_pos_ratio=MODEL["neg_pos_ratio"])

    train_ds = _url_subset(train_ds_full, max_urls) if max_urls else train_ds_full

    n_pos  = sum(1 for i in range(len(train_ds))
                 if int(train_ds[i]["label"].item()) == 1)
    n_neg  = len(train_ds) - n_pos
    print(f"Train pairs: {len(train_ds)}  ({n_pos} pos, {n_neg} neg, ratio {n_neg/max(n_pos,1):.1f}:1)")
    print(f"Val   pairs: {len(val_ds)}")

    train_loader = DataLoader(train_ds, batch_size=MODEL["batch_size"],
                              shuffle=True, num_workers=0)
    val_loader   = DataLoader(val_ds,   batch_size=MODEL["batch_size"],
                              shuffle=False, num_workers=0)

    # ── Model ─────────────────────────────────────────────────────────────
    model     = DualCNNCorrelator().to(device)
    optimizer = torch.optim.Adam(model.parameters(),
                                 lr=MODEL["learning_rate"],
                                 weight_decay=MODEL["weight_decay"])
    # pos_weight=10 matches ShYSh's 10:1 neg:pos ratio weighting.
    # BCEWithLogitsLoss fuses sigmoid + BCE for numerical stability.
    pos_weight = torch.tensor([float(MODEL["neg_pos_ratio"])]).to(device)
    criterion  = nn.BCEWithLogitsLoss(pos_weight=pos_weight)

    best_val_prauc = -1.0
    output_dir.mkdir(parents=True, exist_ok=True)
    ckpt_path = output_dir / f"{mode}_best.pt"

    # ── Epoch loop ────────────────────────────────────────────────────────
    first_loss = None
    for epoch in range(1, n_epochs + 1):
        train_loss, train_scores, train_labels = run_epoch(
            model, train_loader, criterion, device, optimizer, label="train"
        )
        val_loss, val_scores, val_labels = run_epoch(
            model, val_loader, criterion, device, optimizer=None, label="val"
        )

        if first_loss is None:
            first_loss = train_loss

        val_prauc   = compute_pr_auc(val_scores, val_labels)
        train_prauc = compute_pr_auc(train_scores, train_labels)

        print(f"Epoch {epoch:3d}/{n_epochs}  "
              f"train_loss={train_loss:.4f}  val_loss={val_loss:.4f}  "
              f"train_PR-AUC={train_prauc:.4f}  val_PR-AUC={val_prauc:.4f}")

        if val_prauc > best_val_prauc:
            best_val_prauc = val_prauc
            torch.save(model.state_dict(), ckpt_path)

    last_loss = train_loss  # noqa: F821 — always set after ≥1 epoch
    delta = first_loss - last_loss
    trend = "decreased" if delta > 0 else "increased" if delta < 0 else "unchanged"
    print(f"\nLoss {trend}: {first_loss:.4f} → {last_loss:.4f}  (Δ={delta:+.4f})")
    print(f"Best val PR-AUC: {best_val_prauc:.4f}")
    if n_epochs > 1:
        print(f"Checkpoint saved to: {ckpt_path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Train DualCNNCorrelator on a .npz quartet dataset"
    )
    parser.add_argument("--dataset",   required=True,
                        help="Path to .npz produced by dataset_builder.py")
    parser.add_argument("--mode",      required=True,
                        choices=["baseline", "tor", "vpn", "nym5", "nym2"])
    parser.add_argument("--output",    default="./results",
                        help="Directory for checkpoints and logs")
    parser.add_argument("--epochs",    type=int, default=MODEL["epochs"])
    parser.add_argument("--max_urls",  type=int, default=None,
                        help="Limit training to the first N URLs (for sanity checks)")
    args = parser.parse_args()

    train(args.dataset, args.mode, Path(args.output),
          max_urls=args.max_urls, n_epochs=args.epochs)
