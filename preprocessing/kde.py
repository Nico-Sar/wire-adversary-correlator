"""
preprocessing/kde.py
====================
Gaussian KDE transform: packet timestamps → continuous density wave.
Follows the ShYSh shape computation exactly:
  - Convolves a sum of Dirac deltas (one per packet) with a Gaussian kernel
  - Evaluated on a regular time grid with sampling period T
  - Normalized so that sum(shape) == N_F, the packet count within the
    analysis window (ShYSh Eq. 3) — amplitude encodes packet volume
Reference: ShYSh paper, Section III-A "Flow Shape Signal Computation"
  sigma = 0.125s, T = 0.1s (defaults — tune for TCP layer)
"""

import numpy as np

from config.kde_params import KDE


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
    Normalized so that sum(output) == N_F, the number of packets landing
    inside [0, duration) (ShYSh Eq. 3: S[n] = (N_F / sum_k S(kT)) * S(nT)).
    Amplitude therefore encodes real packet volume, not just relative
    timing — restored 2026-07-23 after commit cf7735677 (2026-04-29) had
    switched this to a size-invariant sum(output)==n_samples convention
    instead. That commit's message describes an unrelated feature bundle
    (5-mode coordinator, nym2 WireGuard capture, etc.) and says nothing
    about normalization; the only justification was a docstring comment
    added in the same commit, with no design doc or issue backing it, and
    it contradicts Documents/PIPELINE_AUDIT.md (2026-04-17, 12 days
    earlier — Q3: "KDE normalization -- is sum(KDE) ~ N_packets correct?
    Yes."), which had already reviewed and confirmed the original
    sum(output)==N_F behavior. Predates (not follows) the per-mode
    sigma/d retuning in config/kde_params.py (2026-07-12) by about 2.5
    months — drift riding along in an unrelated commit, not a co-designed
    choice.

    NOTE: N_F counts only timestamps within [0, duration), NOT
    len(timestamps) — callers (quartet_builder.py) carve a much wider
    window than `duration` before calling this function (visits can span
    well beyond the analysis window, e.g. nym5 up to ~140s against a 30s
    duration), so len(timestamps) would overcount for any flow with
    activity extending past the window.

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
    n_f = int(np.sum((t_arr >= 0.0) & (t_arr < duration)))

    if n_f == 0:
        return np.zeros(n_samples, dtype=np.float32)

    # Extend grid by 3σ on each side so boundary packets get full kernel support.
    n_pad   = int(np.ceil(3.0 * sigma / t_sample))    # padding samples per side
    n_total = n_samples + 2 * n_pad
    # Grid starts at −n_pad × t_sample (i.e. before t=0)
    grid = (np.arange(n_total) - n_pad) * t_sample    # shape: (n_total,)

    # Each packet is a Dirac delta convolved with a Gaussian kernel.
    diff   = grid[:, None] - t_arr[None, :]            # (n_total, n_packets)
    kernel = np.exp(-0.5 * (diff / sigma) ** 2)        # (n_total, n_packets)
    shape  = kernel.sum(axis=1)                         # (n_total,)

    # Crop: slice [n_pad : n_pad + n_samples] corresponds exactly to t ∈ [0, duration)
    shape = shape[n_pad : n_pad + n_samples]

    # Normalize: sum(shape) == N_F (ShYSh Eq. 3).
    raw_sum = shape.sum()
    if raw_sum > 0:
        shape = shape * (n_f / raw_sum)

    return shape.astype(np.float32)