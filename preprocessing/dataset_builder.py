"""
preprocessing/dataset_builder.py
=================================
End-to-end pipeline: metadata JSONL + pcap files → .npz dataset.

Reads label logger output (visit_id, url, mode, t_start, t_end),
pairs with the corresponding rotating pcap files from both routers,
computes Quartets, and saves a compressed numpy archive.

Output .npz arrays:
  X_ingress_up    (N, n_windows, L)
  X_ingress_down  (N, n_windows, L)
  X_egress_up     (N, n_windows, L)
  X_egress_down   (N, n_windows, L)
  visit_ids       (N,)
  urls            (N,)
  modes           (N,)
"""

import argparse
from pathlib import Path
from typing import Optional

import numpy as np


def find_pcap_for_window(pcap_dir: Path, router: str,
                          t_start: float, t_end: float) -> str | None:
    """
    Locates the rotating pcap file that covers [t_start, t_end] for the
    given router ('ingress' or 'egress'). Rotating pcaps are named by
    start timestamp, so this is a simple range lookup.
    """
    raise NotImplementedError


def build_dataset(labels_jsonl:  str,
                  ingress_dir:   str,
                  egress_dir:    str,
                  output_path:   str,
                  mode_filter:   Optional[str] = None,
                  **kde_kwargs):
    """
    Main entry point. Reads labels, finds pcaps, computes Quartets,
    pads to uniform window count, and saves compressed .npz.
    """
    raise NotImplementedError


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels",      required=True)
    parser.add_argument("--ingress_dir", required=True)
    parser.add_argument("--egress_dir",  required=True)
    parser.add_argument("--output",      required=True)
    parser.add_argument("--mode",        default=None)
    parser.add_argument("--sigma",       type=float, default=0.125)
    parser.add_argument("--t_sample",    type=float, default=0.1)
    parser.add_argument("--window",      type=int,   default=30)
    parser.add_argument("--duration",    type=float, default=60.0)
    args = parser.parse_args()

    build_dataset(
        args.labels, args.ingress_dir, args.egress_dir, args.output,
        mode_filter=args.mode,
        sigma=args.sigma, t_sample=args.t_sample,
        window_len=args.window, duration=args.duration,
    )
