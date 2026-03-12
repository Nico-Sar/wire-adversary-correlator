"""
analysis/visualize_shapes.py
============================
Plots the KDE shape signals for a given flow pair side-by-side.
Used for sanity checking that the KDE pipeline produces meaningful
density waves before committing to model training.

Usage:
  python analysis/visualize_shapes.py \
      --ingress data/raw/abc123_nym_ingress.pcap \
      --egress  data/raw/abc123_nym_egress.pcap \
      --mode nym
"""

import argparse
import matplotlib.pyplot as plt
import numpy as np


def plot_quartet(ingress_pcap: str, egress_pcap: str,
                 mode: str, output_path: str | None = None):
    """
    Plots all four KDE streams (ingress up/down, egress up/down)
    in a 2x2 grid for visual inspection of shape similarity.
    """
    raise NotImplementedError


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ingress", required=True)
    parser.add_argument("--egress",  required=True)
    parser.add_argument("--mode",    required=True)
    parser.add_argument("--output",  default=None, help="Save plot to file instead of showing")
    args = parser.parse_args()

    plot_quartet(args.ingress, args.egress, args.mode, args.output)
