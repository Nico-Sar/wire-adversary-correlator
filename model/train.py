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
  python -m model.train --dataset data/vpn_dataset.npz --mode vpn

  # Quick sanity check (first 10 URLs, 1 epoch):
  python -m model.train --dataset data/vpn_dataset.npz --mode vpn \\
      --max_urls 10 --epochs 1

  # Multi-seed run with resumable checkpointing (VSC job array):
  python -m model.train --dataset data/vpn_dataset.npz --mode vpn --seed 3 \\
      --output results/vpn_seed3

Seed and checkpoint/resume (added 2026-07-27 for the VSC multi-seed run)
--------------------------------------------------------------------------
--seed controls both the model's own init/data-shuffling RNG
(torch.manual_seed) and QuartetDataset's negative-sampling RNG. Previously
QuartetDataset's seed silently defaulted to a hardcoded 42 on every run and
nothing seeded torch at all, so repeated "pilot" runs weren't actually
varying anything in a controlled way -- confirmed empirically (a repeated
baseline run landed within noise of, not identical to, the original, from
uncontrolled torch RNG state alone).

A resumable checkpoint (model state, optimizer state, epoch, RNG state,
best-so-far) is written to output_dir/{mode}_resume.pt every epoch,
separate from the best-val-PR-AUC checkpoint -- VSC jobs die at walltime,
so resuming from an arbitrary epoch (not just the best one) is required for
an unattended array job. If a resume checkpoint exists at start, training
continues from it automatically. This mechanism (including the kill/resume
behavior itself) was validated on amazone before being ported here.
"""

import argparse
import time
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
          n_epochs:     int = MODEL["epochs"],
          seed:         int = 42,
          num_threads:  int | None = None) -> None:
    """
    Full training loop with train/val split.
    Saves best checkpoint by val PR-AUC to output_dir/{mode}_best.pt, and a
    resumable checkpoint (every epoch) to output_dir/{mode}_resume.pt.
    """
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type == "cpu" and num_threads:
        # Explicit call, not just OMP_NUM_THREADS/MKL_NUM_THREADS env vars --
        # this is what was actually kill/resume-tested on amazone (hardcoded
        # torch.set_num_threads(24) there; env-var-only configuration isn't
        # guaranteed to be read before PyTorch's thread pool initializes).
        torch.set_num_threads(num_threads)
    print(f"Device: {device}  seed: {seed}  "
          f"threads: {torch.get_num_threads() if device.type == 'cpu' else 'n/a'}")
    torch.manual_seed(seed)

    output_dir.mkdir(parents=True, exist_ok=True)
    ckpt_path   = output_dir / f"{mode}_best.pt"
    resume_path = output_dir / f"{mode}_resume.pt"

    # ── Datasets ──────────────────────────────────────────────────────────
    train_ds_full = QuartetDataset(dataset_path, split="train",
                                   neg_pos_ratio=MODEL["neg_pos_ratio"], seed=seed)
    val_ds        = QuartetDataset(dataset_path, split="val",
                                   neg_pos_ratio=MODEL["neg_pos_ratio"], seed=seed)

    # Sanity check: verify split URL disjointness in the ACTUALLY LOADED
    # data, not assumed from QuartetDataset's own splitting logic.
    train_urls = set(train_ds_full.pair_urls)
    val_urls   = set(val_ds.pair_urls)
    overlap    = train_urls & val_urls
    if overlap:
        raise RuntimeError(f"URL LEAKAGE: {len(overlap)} URLs appear in both train and val splits: "
                            f"{list(overlap)[:5]}...")
    print(f"Split disjointness OK: train_urls={len(train_urls)} val_urls={len(val_urls)} overlap=0")

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

    start_epoch    = 1
    best_val_prauc = -1.0

    if resume_path.exists():
        ckpt = torch.load(resume_path, map_location=device)
        model.load_state_dict(ckpt["model_state"])
        optimizer.load_state_dict(ckpt["optimizer_state"])
        # .cpu() explicitly: torch.load(..., map_location=device) moves
        # every tensor in the checkpoint dict onto `device`, but the
        # default generator's RNG state is always a CPU concept --
        # set_rng_state() rejects anything that isn't a CPU ByteTensor.
        # Masked on CPU-only runs (map_location=cpu is a no-op there,
        # which is how this shipped untested on amazone/VSC's CPU
        # partition) but breaks on any CUDA device -- caught by testing
        # this locally on a GPU machine before deploying to VSC.
        torch.set_rng_state(ckpt["rng_state"].cpu())
        start_epoch    = ckpt["epoch"] + 1
        best_val_prauc = ckpt["best_val_prauc"]
        print(f"RESUMED from {resume_path}: epoch={ckpt['epoch']} "
              f"best_val_prauc={best_val_prauc:.4f} -> continuing at epoch {start_epoch}")
    else:
        print("No resume checkpoint found -- starting fresh from epoch 1")

    # ── Epoch loop ────────────────────────────────────────────────────────
    first_loss = None
    last_loss = None
    for epoch in range(start_epoch, n_epochs + 1):
        t0 = time.perf_counter()
        train_loss, train_scores, train_labels = run_epoch(
            model, train_loader, criterion, device, optimizer, label="train"
        )
        val_loss, val_scores, val_labels = run_epoch(
            model, val_loader, criterion, device, optimizer=None, label="val"
        )
        wall_s = time.perf_counter() - t0

        if first_loss is None:
            first_loss = train_loss
        last_loss = train_loss

        val_prauc   = compute_pr_auc(val_scores, val_labels)
        train_prauc = compute_pr_auc(train_scores, train_labels)

        print(f"Epoch {epoch:3d}/{n_epochs}  "
              f"train_loss={train_loss:.4f}  val_loss={val_loss:.4f}  "
              f"train_PR-AUC={train_prauc:.4f}  val_PR-AUC={val_prauc:.4f}  "
              f"wall={wall_s:.1f}s")

        if val_prauc > best_val_prauc:
            best_val_prauc = val_prauc
            torch.save(model.state_dict(), ckpt_path)
            print(f"  -> new best val PR-AUC, saved {ckpt_path}")

        # Resumable checkpoint every epoch (small model -- cheap to write every time).
        torch.save({
            "epoch": epoch,
            "model_state": model.state_dict(),
            "optimizer_state": optimizer.state_dict(),
            "rng_state": torch.get_rng_state(),
            "best_val_prauc": best_val_prauc,
        }, resume_path)

    if first_loss is not None and last_loss is not None:
        delta = first_loss - last_loss
        trend = "decreased" if delta > 0 else "increased" if delta < 0 else "unchanged"
        print(f"\nLoss {trend}: {first_loss:.4f} → {last_loss:.4f}  (Δ={delta:+.4f})")
    print(f"Best val PR-AUC: {best_val_prauc:.4f}")
    print(f"Checkpoint saved to: {ckpt_path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Train DualCNNCorrelator on a .npz quartet dataset"
    )
    parser.add_argument("--dataset",   required=True,
                        help="Path to .npz produced by dataset_builder.py")
    parser.add_argument("--mode",      required=True,
                        choices=["tor", "vpn", "nym5", "nym2"])
    parser.add_argument("--output",    default="./results",
                        help="Directory for checkpoints and logs")
    parser.add_argument("--epochs",    type=int, default=MODEL["epochs"])
    parser.add_argument("--max_urls",  type=int, default=None,
                        help="Limit training to the first N URLs (for sanity checks)")
    parser.add_argument("--seed",      type=int, default=42,
                        help="Seeds both torch (init/shuffling) and QuartetDataset's "
                             "negative sampling. Vary this for multi-seed replicate runs.")
    parser.add_argument("--threads",   type=int, default=None,
                        help="Explicit torch.set_num_threads() on CPU (recommended -- "
                             "OMP_NUM_THREADS alone isn't guaranteed to be read before "
                             "PyTorch's thread pool initializes). Defaults to torch's own "
                             "autodetection if unset.")
    args = parser.parse_args()

    train(args.dataset, args.mode, Path(args.output),
          max_urls=args.max_urls, n_epochs=args.epochs, seed=args.seed,
          num_threads=args.threads)
