"""
preprocessing/windower.py
=========================
Slices a 1D KDE shape signal into overlapping windows.
ShYSh defaults: window_len=30 samples (3s), overlap=50%.
"""

import numpy as np

from config.hyperparams import KDE


def slice_windows(signal:     np.ndarray,
                  window_len: int   = KDE["window_len"],
                  overlap:    float = KDE["overlap"]) -> np.ndarray:
    """
    Slices a 1D signal into overlapping windows.
    Args:
        signal:     1D float32 array (KDE shape signal)
        window_len: number of samples per window
        overlap:    fractional overlap (0.5 = 50%)
    Returns:
        2D float32 array of shape (n_windows, window_len)
    """
    if len(signal) < window_len:
        return np.empty((0, window_len), dtype=np.float32)

    step = max(1, int(window_len * (1.0 - overlap)))
    starts = range(0, len(signal) - window_len + 1, step)
    windows = np.array(
        [signal[i : i + window_len] for i in starts],
        dtype=np.float32,
    )
    return windows


def carve_time_window(packets:  list[dict],
                      t_start:  float,
                      t_end:    float) -> list[dict]:
    """
    Filters a packet list to only those within [t_start, t_end].
    Used to carve per-visit windows from always-on pcap captures
    using label logger timestamps.
    Args:
        packets: list of {ts, size, direction} dicts (absolute timestamps)
        t_start: window start (absolute epoch time)
        t_end:   window end   (absolute epoch time)
    Returns:
        Filtered packet list, timestamps normalized to t_start=0
    """
    carved = [
        {**pkt, "ts": pkt["ts"] - t_start}
        for pkt in packets
        if t_start <= pkt["ts"] <= t_end
    ]
    return carved