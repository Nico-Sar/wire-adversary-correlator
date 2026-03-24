"""
preprocessing/quartet_builder.py
=================================
Assembles the full Quartet from a paired (ingress, egress) pcap + label.

Quartet:
  ingress_up    — traffic flowing from client toward the anonymity network
  ingress_down  — traffic flowing from anonymity network back to client
  egress_up     — reconstructed requests arriving at the server side
  egress_down   — server responses flowing back toward the anonymity network

Each stream is a 2D array of shape (n_windows, window_len) after KDE + windowing.
"""

import numpy as np

from preprocessing.kde import kde_shape, split_directions
from preprocessing.pcap_parser import extract_packets
from preprocessing.windower import carve_time_window, slice_windows


def compute_quartet(ingress_pcap:     str,
                    egress_pcap:      str,
                    t_start:          float,
                    t_end:            float,
                    ingress_local_ip: str | None = None,
                    egress_local_ip:  str | None = None,
                    **kde_kwargs) -> dict:
    """
    Given a paired (ingress, egress) pcap and the visit time window [t_start, t_end],
    carves the relevant packets, applies KDE, and returns the Quartet.

    Returns dict with keys:
        ingress_up    (n_windows, L)
        ingress_down  (n_windows, L)
        egress_up     (n_windows, L)
        egress_down   (n_windows, L)
        n_ingress_up, n_ingress_down, n_egress_up, n_egress_down  (int)
    """
    # ── 1. Parse both pcaps ────────────────────────────────────────────────
    ingress_pkts = extract_packets(ingress_pcap, local_ip=ingress_local_ip)
    egress_pkts  = extract_packets(egress_pcap,  local_ip=egress_local_ip)

    # ── 2. Carve the visit time window from the always-on capture ──────────
    # t_start / t_end come from the label logger (absolute epoch timestamps)
    ingress_carved = carve_time_window(ingress_pkts, t_start, t_end)
    egress_carved  = carve_time_window(egress_pkts,  t_start, t_end)

    # ── 3. Split into UP / DOWN streams ───────────────────────────────────
    # After carve_time_window, timestamps are relative (t_start = 0)
    ingress_up_ts,   ingress_down_ts  = split_directions(ingress_carved)
    egress_up_ts,    egress_down_ts   = split_directions(egress_carved)

    # ── 4. KDE shape for each stream ──────────────────────────────────────
    ingress_up_shape   = kde_shape(ingress_up_ts,   **kde_kwargs)
    ingress_down_shape = kde_shape(ingress_down_ts, **kde_kwargs)
    egress_up_shape    = kde_shape(egress_up_ts,    **kde_kwargs)
    egress_down_shape  = kde_shape(egress_down_ts,  **kde_kwargs)

    # ── 5. Slice into overlapping windows ─────────────────────────────────
    ingress_up   = slice_windows(ingress_up_shape)
    ingress_down = slice_windows(ingress_down_shape)
    egress_up    = slice_windows(egress_up_shape)
    egress_down  = slice_windows(egress_down_shape)

    return {
        "ingress_up":      ingress_up,
        "ingress_down":    ingress_down,
        "egress_up":       egress_up,
        "egress_down":     egress_down,
        "n_ingress_up":    len(ingress_up_ts),
        "n_ingress_down":  len(ingress_down_ts),
        "n_egress_up":     len(egress_up_ts),
        "n_egress_down":   len(egress_down_ts),
    }