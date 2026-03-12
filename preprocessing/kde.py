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
    raise NotImplementedError


def split_directions(packets: list[dict]) -> tuple[list[float], list[float]]:
    """
    Splits packet list into (up_timestamps, down_timestamps).
    Only timestamps are used for the density estimate (not sizes).
    Size-weighted KDE is left as a future extension.
    """
    raise NotImplementedError


def kde_shape(timestamps: list[float],
              duration:   float = KDE["duration"],
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
    raise NotImplementedError
