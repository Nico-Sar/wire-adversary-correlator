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
      --labels   data/vpn_visits.jsonl \\
      --data_dir data/vpn \\
      --output   data/vpn_dataset.npz \\
      --mode     vpn
"""

import argparse
import json
import logging
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import numpy as np

from config.hyperparams import KDE, KDE_PER_MODE
from config.infrastructure import EGRESS_ONLY_MODES, get_client_private_ip
from preprocessing.quartet_builder import compute_quartet

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _process_one_visit(rec: dict, data_dir_str: str, mode: str,
                        mode_kde: dict, kde_kwargs: dict) -> dict:
    """
    Worker for one visit -- pure function of its inputs (own pcap pair,
    own record), so it's safe to run in a separate process. Returns a dict
    tagged with status "ok" (quartet + metadata) or "skip" (reason string)
    instead of raising/logging directly, since logging from a child process
    doesn't reach the parent's handlers -- the parent logs skip reasons
    itself once results come back.
    """
    data_dir = Path(data_dir_str)
    visit_id = rec["visit_id"]
    url      = rec["url"]
    for _sep in ("_bf", "_v"):
        if _sep in visit_id:
            client_id = visit_id.split(_sep)[0]
            break
    else:
        client_id = visit_id

    ingress_pcap = data_dir / f"{visit_id}_ingress.pcap"
    egress_pcap  = data_dir / f"{visit_id}_egress.pcap"

    if not ingress_pcap.exists() or not egress_pcap.exists():
        return {"status": "skip", "visit_id": visit_id,
                "reason": f"Missing pcap for {visit_id}"}

    t_start = rec["t_visit_start"]
    t_end   = rec["t_visit_end"]

    try:
        client_ip = get_client_private_ip(client_id)
    except KeyError:
        return {"status": "skip", "visit_id": visit_id,
                "reason": f"Unknown client_id '{client_id}'"}

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
        return {"status": "skip", "visit_id": visit_id,
                "reason": f"Quartet failed for {visit_id}: {e}"}

    min_pkts_per_stream = mode_kde.get("min_packets", KDE["min_packets"])
    stream_counts = {
        "ingress_up":   quartet["n_ingress_up"],
        "ingress_down": quartet["n_ingress_down"],
        "egress_up":    quartet["n_egress_up"],
        "egress_down":  quartet["n_egress_down"],
    }
    checked_streams = (
        ("egress_up", "egress_down")
        if mode in EGRESS_ONLY_MODES
        else ("ingress_up", "ingress_down", "egress_up", "egress_down")
    )
    low_streams = [k for k in checked_streams if stream_counts[k] < min_pkts_per_stream]
    if low_streams:
        return {"status": "skip", "visit_id": visit_id,
                "reason": f"Low per-stream packet count {low_streams} for {visit_id}"}

    zero_streams = [k for k in checked_streams if quartet[k].shape[0] == 0]
    if zero_streams:
        return {"status": "skip", "visit_id": visit_id,
                "reason": f"Zero windows in {zero_streams} for {visit_id}"}

    return {
        "status": "ok",
        "visit_id": visit_id,
        "url": url,
        "ingress_up": quartet["ingress_up"],
        "ingress_down": quartet["ingress_down"],
        "egress_up": quartet["egress_up"],
        "egress_down": quartet["egress_down"],
    }


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
                       (e.g. data/vpn_visits.jsonl)
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
    mode = mode_filter or records[0].get("mode", "vpn")
    mode_kde = {**KDE_PER_MODE.get(mode, KDE), **kde_kwargs}
    log.info(f"Mode: {mode}  KDE params: {mode_kde}")

    # ── 4. Compute Quartet for each visit (parallel — each visit only reads
    #    its own two pcaps and is otherwise independent, see _process_one_visit) ──
    n_workers = min(int(os.environ.get("DATASET_BUILDER_WORKERS", os.cpu_count() or 4)),
                     len(records))
    log.info(f"Processing {len(records)} visits with {n_workers} parallel workers")

    results_by_index = [None] * len(records)
    with ProcessPoolExecutor(max_workers=n_workers) as ex:
        futs = {
            ex.submit(_process_one_visit, rec, str(data_dir), mode, mode_kde, kde_kwargs): i
            for i, rec in enumerate(records)
        }
        done = 0
        for fut in as_completed(futs):
            i = futs[fut]
            results_by_index[i] = fut.result()
            done += 1
            if done % 500 == 0:
                log.info(f"  Processed {done}/{len(records)} visits")

    # Rebuild lists in original record order (order only affects which raw
    # index a visit lands at pre-shuffle below — shuffling then re-derives
    # a random assignment anyway, so this is purely for stable/debuggable
    # output, not a correctness requirement).
    ingress_up_list   = []
    ingress_down_list = []
    egress_up_list    = []
    egress_down_list  = []
    visit_ids_list    = []
    urls_list         = []
    modes_list        = []
    skipped           = 0

    for r in results_by_index:
        if r["status"] == "skip":
            log.warning(f"  {r['reason']} — skipping")
            skipped += 1
            continue
        ingress_up_list.append(r["ingress_up"])
        ingress_down_list.append(r["ingress_down"])
        egress_up_list.append(r["egress_up"])
        egress_down_list.append(r["egress_down"])
        visit_ids_list.append(r["visit_id"])
        urls_list.append(r["url"])
        modes_list.append(mode)

    log.info(f"  Processed {len(records)}/{len(records)} visits ({skipped} skipped)")

    N = len(visit_ids_list)
    log.info(f"Successfully processed {N} visits ({skipped} skipped)")

    if N == 0:
        raise ValueError("No valid visits after processing — cannot build dataset")

    # ── 5. Stack windows — all visits must produce identical window counts ────
    # Within a single mode, kde_shape() returns a fixed-length grid
    # (ceil(duration/t_sample) samples), and slice_windows pads the signal tail
    # so (n_samples - window_len) % step == 0.  Together these guarantee that
    # every visit produces the same n_windows — no zero-padding needed.
    n_windows_all = [a.shape[0] for a in ingress_up_list]
    if len(set(n_windows_all)) != 1:
        raise ValueError(
            f"Inconsistent window counts across visits: {set(n_windows_all)}. "
            "Ensure all visits use the same mode and KDE duration."
        )
    n_windows  = n_windows_all[0]
    window_len = ingress_up_list[0].shape[1]
    log.info(f"Stacking {N} visits × {n_windows} windows × {window_len} samples")

    X_ingress_up   = np.stack(ingress_up_list)
    X_ingress_down = np.stack(ingress_down_list)
    X_egress_up    = np.stack(egress_up_list)
    X_egress_down  = np.stack(egress_down_list)

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
                        help="Filter by mode (tor/vpn/nym5/nym2)")
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