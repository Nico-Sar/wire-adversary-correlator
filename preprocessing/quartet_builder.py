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

from config.kde_params import KDE_PER_MODE
from config.infrastructure import EGRESS_ROUTER
from preprocessing.kde import kde_shape, split_directions
from preprocessing.pcap_parser import extract_packets
from preprocessing.windower import carve_time_window, slice_windows


def compute_quartet(ingress_pcap:      str,
                    egress_pcap:       str,
                    t_start:           float,
                    t_end:             float,
                    client_private_ip: str,
                    mode:              str,
                    **kde_kwargs) -> dict:
    """
    Given a paired (ingress, egress) pcap and the visit time window [t_start, t_end],
    carves the relevant packets, applies KDE, and returns the Quartet.

    Args:
        ingress_pcap:      path to the ingress router pcap
        egress_pcap:       path to the egress router pcap
        t_start:           visit start (absolute epoch timestamp)
        t_end:             visit end   (absolute epoch timestamp)
        client_private_ip: private LAN IP of the visiting client (e.g. "10.0.0.3").
                           Used as local_ip at the ingress capture point so that
                           UP = client→network and DOWN = network→client.
        mode:              anonymity mode ("tor", "nym5", "nym2", "vpn").
                           Selects KDE_PER_MODE defaults for duration and sigma.
        **kde_kwargs:      override any KDE_PER_MODE default explicitly.

    Returns dict with keys:
        ingress_up    (n_windows, L)
        ingress_down  (n_windows, L)
        egress_up     (n_windows, L)
        egress_down   (n_windows, L)
        n_ingress_up, n_ingress_down, n_egress_up, n_egress_down  (int)
        mode          (str)
    """
    ingress_local_ip = client_private_ip
    egress_local_ip  = EGRESS_ROUTER["private_ip"]

    # Merge mode defaults with any caller overrides
    effective_kde = {**KDE_PER_MODE[mode], **kde_kwargs}

    # kde_shape() only accepts duration/sigma/t_sample. effective_kde can carry
    # extra mode-specific keys not meant for it — e.g. nym2's min_packets,
    # which exists purely for dataset_builder.py's own per-stream threshold
    # gate (read directly from KDE_PER_MODE there, untouched by this filter).
    # Forwarding effective_kde unfiltered raised TypeError on every nym2 call
    # (min_packets is not a kde_shape() parameter), silently swallowed by
    # dataset_builder's generic except as a false "quartet failed" skip —
    # zero nym2 visits ever got built. Modes without extra keys are unaffected.
    kde_only = {k: v for k, v in effective_kde.items() if k in ("duration", "sigma", "t_sample")}

    # ── 1. Parse both pcaps ────────────────────────────────────────────────
    ingress_pkts = extract_packets(ingress_pcap, local_ip=ingress_local_ip)
    egress_pkts  = extract_packets(egress_pcap,  local_ip=egress_local_ip)

    # ── 2. Carve the visit time window with a small buffer ─────────────────
    # Buffer: -0.5 s before start (capture latency), +3.0 s after end
    # (tail traffic from server responses still in flight).
    ingress_carved = carve_time_window(ingress_pkts, t_start - 0.5, t_end + 3.0)
    egress_carved  = carve_time_window(egress_pkts,  t_start - 0.5, t_end + 3.0)

    # ── 3. Split into UP / DOWN streams ───────────────────────────────────
    # After carve_time_window, timestamps are relative (t_start = 0)
    ingress_up_ts,   ingress_down_ts  = split_directions(ingress_carved)
    egress_up_ts,    egress_down_ts   = split_directions(egress_carved)

    # ── 4. KDE shape for each stream ──────────────────────────────────────
    ingress_up_shape   = kde_shape(ingress_up_ts,   **kde_only)
    ingress_down_shape = kde_shape(ingress_down_ts, **kde_only)
    egress_up_shape    = kde_shape(egress_up_ts,    **kde_only)
    egress_down_shape  = kde_shape(egress_down_ts,  **kde_only)

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
        "mode":            mode,
    }
