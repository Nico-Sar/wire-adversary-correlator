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


def compute_quartet(ingress_pcap:    str,
                    egress_pcap:     str,
                    t_start:         float,
                    t_end:           float,
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
    raise NotImplementedError
