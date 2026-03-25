"""
preprocessing/kde.py
====================
Gaussian KDE transform: packet timestamps → continuous density wave.
Follows the ShYSh shape computation exactly:
  - Convolves a sum of Dirac deltas (one per packet) with a Gaussian kernel
  - Evaluated on a regular time grid with sampling period T
  - Normalized so that sum(shape) == number of packets in the flow
Reference: ShYSh paper, Section III-A "Flow Shape Signal Computation"
  sigma = 0.125s, T = 0.1s (defaults — tune for TCP layer)
"""

import numpy as np

from config.hyperparams import KDE


def normalize_timestamps(packets: list[dict]) -> list[dict]:
    """
    Converts absolute epoch timestamps to relative timestamps starting at t=0.
    Mirrors ShYSh's use of relative timestamps for alignment independence.
    """
    if not packets:
        return []

    t0 = packets[0]["ts"]
    return [
        {**pkt, "ts": pkt["ts"] - t0}
        for pkt in packets
    ]


def split_directions(packets: list[dict]) -> tuple[list[float], list[float]]:
    """
    Splits packet list into (up_timestamps, down_timestamps).
    Only timestamps are used for the density estimate (not sizes).
    Size-weighted KDE is left as a future extension.
    """
    up   = [pkt["ts"] for pkt in packets if pkt["direction"] == +1]
    down = [pkt["ts"] for pkt in packets if pkt["direction"] == -1]
    return up, down


def kde_shape(timestamps: list[float],
              duration:   float,
              sigma:      float = KDE["sigma"],
              t_sample:   float = KDE["t_sample"]) -> np.ndarray:
    """
    Computes the KDE shape signal from a list of packet timestamps.
    Returns a 1D float32 array of length ceil(duration / t_sample).
    Normalized so that sum(output) == len(timestamps).

    Args:
        timestamps: list of relative packet arrival times in seconds
        duration:   max flow duration to analyze (seconds)
        sigma:      Gaussian kernel width (seconds)
        t_sample:   grid sampling period (seconds)
    """
    n_samples = int(np.ceil(duration / t_sample))
    grid = np.arange(n_samples) * t_sample   # shape: (n_samples,)

    if not timestamps:
        return np.zeros(n_samples, dtype=np.float32)

    # Each packet is a Dirac delta convolved with a Gaussian kernel.
    # Vectorised: for each grid point t, sum Gaussian(t - t_i) over all packets.
    # Shape: (n_samples, 1) - (1, n_packets) → (n_samples, n_packets)
    t_arr  = np.array(timestamps, dtype=np.float64)        # (n_packets,)
    diff   = grid[:, None] - t_arr[None, :]                # (n_samples, n_packets)
    kernel = np.exp(-0.5 * (diff / sigma) ** 2)            # (n_samples, n_packets)
    shape  = kernel.sum(axis=1)                             # (n_samples,)

    # Normalize: sum(shape) == len(timestamps)
    # The raw sum of the unnormalized Gaussian is sigma * sqrt(2π) per packet.
    # We divide by that factor to recover the correct packet count integral.
    norm_factor = sigma * np.sqrt(2.0 * np.pi) / t_sample
    if norm_factor > 0:
        shape /= norm_factor

    return shape.astype(np.float32)