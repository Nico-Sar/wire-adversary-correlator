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

    NOTE: Not used in the main pipeline. Timestamp normalization is handled by
    carve_time_window() in windower.py, which re-zeros timestamps after carving
    the visit window. This function is kept because tests/test_kde.py covers it.
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

    Implementation note:
        The grid is extended by 3σ on each side before computing the Gaussian
        kernels, then cropped back to [0, duration]. This prevents boundary
        truncation of Gaussian tails for packets near t=0 or t=duration
        (most significant for Nym where σ = 0.5 s).
    """
    n_samples = int(np.ceil(duration / t_sample))

    if not timestamps:
        return np.zeros(n_samples, dtype=np.float32)

    t_arr = np.array(timestamps, dtype=np.float64)

    # Extend grid by 3σ on each side so boundary packets get full kernel support.
    n_pad   = int(np.ceil(3.0 * sigma / t_sample))    # padding samples per side
    n_total = n_samples + 2 * n_pad
    # Grid starts at −n_pad × t_sample (i.e. before t=0)
    grid = (np.arange(n_total) - n_pad) * t_sample    # shape: (n_total,)

    # Each packet is a Dirac delta convolved with a Gaussian kernel.
    diff   = grid[:, None] - t_arr[None, :]            # (n_total, n_packets)
    kernel = np.exp(-0.5 * (diff / sigma) ** 2)        # (n_total, n_packets)
    shape  = kernel.sum(axis=1)                         # (n_total,)

    # Normalize: sum(shape) == len(timestamps)
    # The raw sum of the unnormalized Gaussian is sigma * sqrt(2π) per packet.
    norm_factor = sigma * np.sqrt(2.0 * np.pi) / t_sample
    shape /= norm_factor

    # Crop: slice [n_pad : n_pad + n_samples] corresponds exactly to t ∈ [0, duration)
    return shape[n_pad : n_pad + n_samples].astype(np.float32)