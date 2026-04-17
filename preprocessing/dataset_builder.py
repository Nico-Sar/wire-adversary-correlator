"""
preprocessing/dataset_builder.py
=================================
End-to-end pipeline: coordinator JSONL + per-visit pcap files → .npz dataset.

Design principles:
  - Per-visit pcap model: coordinator saves {visit_id}_ingress.pcap and
    {visit_id}_egress.pcap for each visit. No rotating pcap search needed.
  - Leakage prevention: ingress and egress arrays are shuffled independently
    so no positional correspondence exists in the raw arrays. The pairs array
    records (ingress_idx, egress_idx, label=1) for positive pairs only.
    Negative pairs are generated at training time in dataset.py.
  - Split by URL: dataset.py recomputes the 70/15/15 train/val/test split
    from ingress_urls/egress_urls at training time, following ShYSh.

Output .npz arrays:
  X_ingress_up      (N, n_windows, L)  float32
  X_ingress_down    (N, n_windows, L)  float32
  X_egress_up       (N, n_windows, L)  float32
  X_egress_down     (N, n_windows, L)  float32
  ingress_visit_ids (N,)               str
  egress_visit_ids  (N,)               str
  ingress_urls      (N,)               str
  egress_urls       (N,)               str
  pairs             (N, 3)             int32 (ingress_idx, egress_idx, label)
  modes             (N,)               str

Usage:
  python -m preprocessing.dataset_builder \\
      --labels   data/baseline_visits.jsonl \\
      --data_dir data/baseline \\
      --output   data/baseline_dataset.npz \\
      --mode     baseline
"""

import argparse
import json
import logging
from pathlib import Path
from typing import Optional

import numpy as np

from config.hyperparams import KDE, KDE_PER_MODE
from config.infrastructure import get_client_private_ip
from preprocessing.quartet_builder import compute_quartet

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def build_dataset(labels_jsonl: str,
                  data_dir:     str,
                  output_path:  str,
                  mode_filter:  Optional[str] = None,
                  seed:         int = 42,
                  **kde_kwargs):
    """
    Main entry point.

    Reads the coordinator JSONL, locates per-visit pcap pairs, computes
    Quartets, applies independent ingress/egress shuffling for leakage
    prevention, and saves a compressed .npz archive.

    Args:
        labels_jsonl:  path to coordinator output JSONL
                       (e.g. data/baseline_visits.jsonl)
        data_dir:      directory containing {visit_id}_ingress.pcap and
                       {visit_id}_egress.pcap files
        output_path:   path for the output .npz file
        mode_filter:   if set, only process records with this mode
        seed:          random seed for reproducible shuffling
        **kde_kwargs:  override KDE parameters (sigma, duration, etc.)
    """
    data_dir = Path(data_dir)
    rng      = np.random.default_rng(seed)

    # ── 1. Read and filter JSONL records ──────────────────────────────────
    records = []
    with open(labels_jsonl) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("visit_status") != "success":
                log.debug(f"Skipping failed visit {rec.get('visit_id')}")
                continue
            if mode_filter and rec.get("mode") != mode_filter:
                continue
            records.append(rec)

    if not records:
        raise ValueError(
            f"No successful records found in {labels_jsonl}"
            + (f" for mode={mode_filter}" if mode_filter else "")
        )

    log.info(f"Found {len(records)} successful visits to process")

    # ── 2. Log unique URL count ───────────────────────────────────────────
    # dataset.py recomputes the 70/15/15 URL-based split from ingress_urls /
    # egress_urls directly, so no pre-computed index needs to be saved here.
    all_urls = sorted(set(rec["url"] for rec in records))
    log.info(f"Dataset contains {len(all_urls)} unique URLs")

    # ── 3. Infer mode for KDE params ──────────────────────────────────────
    mode = mode_filter or records[0].get("mode", "baseline")
    mode_kde = {**KDE_PER_MODE.get(mode, KDE), **kde_kwargs}
    log.info(f"Mode: {mode}  KDE params: {mode_kde}")

    # ── 4. Compute Quartet for each visit ─────────────────────────────────
    ingress_up_list   = []
    ingress_down_list = []
    egress_up_list    = []
    egress_down_list  = []
    visit_ids_list    = []
    urls_list         = []
    modes_list        = []
    skipped           = 0

    for i, rec in enumerate(records):
        visit_id  = rec["visit_id"]
        url       = rec["url"]
        client_id = visit_id.split("_v")[0]   # e.g. "tor-client1"

        ingress_pcap = data_dir / f"{visit_id}_ingress.pcap"
        egress_pcap  = data_dir / f"{visit_id}_egress.pcap"

        if not ingress_pcap.exists() or not egress_pcap.exists():
            log.warning(f"  [{i+1}/{len(records)}] Missing pcap for {visit_id} — skipping")
            skipped += 1
            continue

        # Use visit window with buffer (already applied in quartet_builder)
        t_start = rec["t_visit_start"]
        t_end   = rec["t_visit_end"]

        try:
            client_ip = get_client_private_ip(client_id)
        except KeyError:
            log.warning(f"  [{i+1}/{len(records)}] Unknown client_id '{client_id}' — skipping")
            skipped += 1
            continue

        try:
            quartet = compute_quartet(
                ingress_pcap=str(ingress_pcap),
                egress_pcap=str(egress_pcap),
                t_start=t_start,
                t_end=t_end,
                client_private_ip=client_ip,
                mode=mode,
                **kde_kwargs,
            )
        except Exception as e:
            log.warning(f"  [{i+1}/{len(records)}] Quartet failed for {visit_id}: {e} — skipping")
            skipped += 1
            continue

        # Check minimum packet count per stream (not in total).
        # A visit where one stream is empty produces all-zero windows for that
        # stream, which misleads training with corrupted positive pairs.
        min_pkts_per_stream = KDE["min_packets"]  # 5, from config/hyperparams.py
        stream_counts = {
            "ingress_up":   quartet["n_ingress_up"],
            "ingress_down": quartet["n_ingress_down"],
            "egress_up":    quartet["n_egress_up"],
            "egress_down":  quartet["n_egress_down"],
        }
        low_streams = [k for k, v in stream_counts.items() if v < min_pkts_per_stream]
        if low_streams:
            log.warning(
                f"  [{i+1}/{len(records)}] Low per-stream packet count "
                f"{low_streams} for {visit_id} — skipping"
            )
            skipped += 1
            continue

        # Guard: all four streams must have produced at least one window.
        # slice_windows returns shape (0, L) if the KDE signal is shorter than
        # window_len (3 s). A zero-window visit would be padded to all-zeros
        # and labelled as a positive pair, corrupting training.
        zero_streams = [
            k for k in ("ingress_up", "ingress_down", "egress_up", "egress_down")
            if quartet[k].shape[0] == 0
        ]
        if zero_streams:
            log.warning(
                f"  [{i+1}/{len(records)}] Zero windows in {zero_streams} "
                f"for {visit_id} — skipping"
            )
            skipped += 1
            continue

        ingress_up_list.append(quartet["ingress_up"])
        ingress_down_list.append(quartet["ingress_down"])
        egress_up_list.append(quartet["egress_up"])
        egress_down_list.append(quartet["egress_down"])
        visit_ids_list.append(visit_id)
        urls_list.append(url)
        modes_list.append(mode)

        if (i + 1) % 50 == 0:
            log.info(f"  Processed {i+1}/{len(records)} visits ({skipped} skipped)")

    N = len(visit_ids_list)
    log.info(f"Successfully processed {N} visits ({skipped} skipped)")

    if N == 0:
        raise ValueError("No valid visits after processing — cannot build dataset")

    # ── 5. Pad windows to uniform shape ──────────────────────────────────
    # Different visits may have different n_windows due to variable flow
    # duration. Pad with zeros to the maximum window count.
    max_windows = max(
        max(a.shape[0] for a in ingress_up_list),
        max(a.shape[0] for a in ingress_down_list),
        max(a.shape[0] for a in egress_up_list),
        max(a.shape[0] for a in egress_down_list),
    )
    window_len = ingress_up_list[0].shape[1]
    log.info(f"Padding to {max_windows} windows × {window_len} samples")

    def pad_to(arrays, n_windows, wlen):
        out = np.zeros((len(arrays), n_windows, wlen), dtype=np.float32)
        for i, a in enumerate(arrays):
            out[i, :a.shape[0], :] = a
        return out

    X_ingress_up   = pad_to(ingress_up_list,   max_windows, window_len)
    X_ingress_down = pad_to(ingress_down_list, max_windows, window_len)
    X_egress_up    = pad_to(egress_up_list,    max_windows, window_len)
    X_egress_down  = pad_to(egress_down_list,  max_windows, window_len)

    visit_ids = np.array(visit_ids_list)
    urls      = np.array(urls_list)
    modes_arr = np.array(modes_list)

    # ── 6. Independent shuffle for leakage prevention ────────────────────
    # After shuffling, paired flows are at different row indices.
    # The pairs array records which indices correspond.
    ingress_order = rng.permutation(N)
    egress_order  = rng.permutation(N)

    X_ingress_up   = X_ingress_up[ingress_order]
    X_ingress_down = X_ingress_down[ingress_order]
    X_egress_up    = X_egress_up[egress_order]
    X_egress_down  = X_egress_down[egress_order]

    ingress_visit_ids = visit_ids[ingress_order]
    egress_visit_ids  = visit_ids[egress_order]
    ingress_urls      = urls[ingress_order]
    egress_urls       = urls[egress_order]

    # ── 7. Build positive pairs index ────────────────────────────────────
    ingress_id_to_idx = {vid: idx for idx, vid in enumerate(ingress_visit_ids)}
    egress_id_to_idx  = {vid: idx for idx, vid in enumerate(egress_visit_ids)}

    pairs = np.array(
        [[ingress_id_to_idx[vid], egress_id_to_idx[vid], 1]
         for vid in visit_ids_list],
        dtype=np.int32,
    )

    log.info(f"Built {len(pairs)} positive pairs")

    # ── 8. Save .npz ──────────────────────────────────────────────────────
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        output_path,
        X_ingress_up=X_ingress_up,
        X_ingress_down=X_ingress_down,
        X_egress_up=X_egress_up,
        X_egress_down=X_egress_down,
        ingress_visit_ids=ingress_visit_ids,
        egress_visit_ids=egress_visit_ids,
        ingress_urls=ingress_urls,
        egress_urls=egress_urls,
        pairs=pairs,
        modes=modes_arr[ingress_order],
    )

    size_mb = Path(output_path).stat().st_size / 1e6
    log.info(f"Saved {output_path}  ({size_mb:.1f} MB)")
    log.info(f"Array shapes: X_ingress_up={X_ingress_up.shape}  pairs={pairs.shape}")
    return output_path


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Build .npz dataset from coordinator JSONL + pcap files"
    )
    parser.add_argument("--labels",   required=True,
                        help="Path to coordinator output JSONL")
    parser.add_argument("--data_dir", required=True,
                        help="Directory containing {visit_id}_ingress/egress.pcap files")
    parser.add_argument("--output",   required=True,
                        help="Output .npz path")
    parser.add_argument("--mode",     default=None,
                        help="Filter by mode (baseline/tor/vpn/nym)")
    parser.add_argument("--seed",     type=int,   default=42)
    parser.add_argument("--sigma",    type=float, default=None)
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--window",   type=int,   default=None)
    args = parser.parse_args()

    kde_overrides = {}
    if args.sigma    is not None: kde_overrides["sigma"]      = args.sigma
    if args.duration is not None: kde_overrides["duration"]   = args.duration
    if args.window   is not None: kde_overrides["window_len"] = args.window

    build_dataset(
        labels_jsonl=args.labels,
        data_dir=args.data_dir,
        output_path=args.output,
        mode_filter=args.mode,
        seed=args.seed,
        **kde_overrides,
    )