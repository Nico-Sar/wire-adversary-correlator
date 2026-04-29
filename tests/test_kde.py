"""
tests/test_kde.py
=================
Unit tests for the Gaussian KDE preprocessing module.

These tests validate the mathematical properties of the KDE output
before any real capture data is available. Run with:
  pytest tests/test_kde.py -v
"""

import numpy as np
import pytest

# Once preprocessing/kde.py is implemented, these imports will work.
# Until then, tests will be collected but skipped with NotImplementedError.
from preprocessing.kde import kde_shape, normalize_timestamps, split_directions


class TestNormalizeTimestamps:

    def test_first_timestamp_is_zero(self):
        packets = [
            {"ts": 100.0, "size": 64, "direction": 1},
            {"ts": 100.5, "size": 64, "direction": -1},
            {"ts": 101.2, "size": 64, "direction": 1},
        ]
        result = normalize_timestamps(packets)
        assert result[0]["ts"] == pytest.approx(0.0)

    def test_relative_gaps_preserved(self):
        packets = [
            {"ts": 100.0, "size": 64, "direction": 1},
            {"ts": 100.5, "size": 64, "direction": -1},
            {"ts": 101.2, "size": 64, "direction": 1},
        ]
        result = normalize_timestamps(packets)
        assert result[1]["ts"] == pytest.approx(0.5)
        assert result[2]["ts"] == pytest.approx(1.2)

    def test_other_fields_unchanged(self):
        packets = [{"ts": 50.0, "size": 128, "direction": -1}]
        result = normalize_timestamps(packets)
        assert result[0]["size"] == 128
        assert result[0]["direction"] == -1

    def test_empty_list(self):
        assert normalize_timestamps([]) == []


class TestSplitDirections:

    def test_correct_split(self):
        packets = [
            {"ts": 0.0, "size": 64, "direction":  1},
            {"ts": 0.1, "size": 64, "direction": -1},
            {"ts": 0.2, "size": 64, "direction":  1},
            {"ts": 0.3, "size": 64, "direction": -1},
            {"ts": 0.4, "size": 64, "direction": -1},
        ]
        up, down = split_directions(packets)
        assert len(up)   == 2
        assert len(down) == 3

    def test_timestamps_returned_not_packets(self):
        packets = [{"ts": 1.5, "size": 64, "direction": 1}]
        up, down = split_directions(packets)
        assert up[0] == pytest.approx(1.5)

    def test_all_up(self):
        packets = [{"ts": float(i), "size": 64, "direction": 1} for i in range(5)]
        up, down = split_directions(packets)
        assert len(up) == 5
        assert len(down) == 0

    def test_empty(self):
        up, down = split_directions([])
        assert up == []
        assert down == []


class TestKdeShape:

    def test_output_length(self):
        """Output length should be ceil(duration / t_sample)."""
        import math
        ts = [0.5, 1.0, 2.0, 3.0]
        shape = kde_shape(ts, duration=6.0, sigma=0.125, t_sample=0.1)
        expected_len = math.ceil(6.0 / 0.1)
        assert len(shape) == expected_len

    def test_normalization(self):
        """sum(shape) should equal n_grid_samples (ShYSh convention)."""
        ts = [0.5, 1.0, 1.5, 2.0, 2.5]
        duration, t_sample = 6.0, 0.1
        shape = kde_shape(ts, duration=duration, sigma=0.125, t_sample=t_sample)
        n_samples = int(np.ceil(duration / t_sample))
        assert np.sum(shape) == pytest.approx(n_samples, rel=1e-3)

    def test_empty_timestamps_returns_zeros(self):
        shape = kde_shape([], duration=6.0, sigma=0.125, t_sample=0.1)
        assert np.all(shape == 0.0)

    def test_output_dtype_is_float32(self):
        ts = [0.5, 1.0, 2.0]
        shape = kde_shape(ts, duration=6.0, sigma=0.125, t_sample=0.1)
        assert shape.dtype == np.float32

    def test_output_is_nonnegative(self):
        ts = [0.1, 0.5, 1.0, 2.0]
        shape = kde_shape(ts, duration=6.0, sigma=0.125, t_sample=0.1)
        assert np.all(shape >= 0.0)

    def test_peak_near_packet_location(self):
        """KDE should produce a peak close to where the packet landed."""
        ts = [3.0]  # Single packet at t=3.0
        shape = kde_shape(ts, duration=6.0, sigma=0.125, t_sample=0.1)
        peak_idx = np.argmax(shape)
        peak_time = peak_idx * 0.1
        assert abs(peak_time - 3.0) < 0.2  # Within 2 samples of true location

    def test_timestamps_outside_duration_ignored(self):
        """A packet far outside the window should not affect KDE values within it."""
        ts_inside  = [1.0, 2.0, 3.0]
        ts_outside = [1.0, 2.0, 3.0, 999.0]  # One packet way outside
        shape_in  = kde_shape(ts_inside,  duration=60.0, sigma=0.125, t_sample=0.1)
        shape_out = kde_shape(ts_outside, duration=60.0, sigma=0.125, t_sample=0.1)
        # The Gaussian kernel has infinite support, so normalizing and comparing
        # overall shapes fails. Instead verify that the KDE density at each
        # in-window packet location is unaffected by the far-out-of-window packet.
        # With sigma=0.125 s, the kernel contribution from t=999 at t<=60 is
        # exp(-0.5 * (939/0.125)^2) ≈ 0, well below float32 precision.
        for t in [1.0, 2.0, 3.0]:
            idx = int(t / 0.1)
            assert shape_in[idx] == pytest.approx(shape_out[idx], rel=1e-3)

    def test_sigma_controls_smoothing(self):
        """Larger sigma should produce a flatter (less spiky) distribution."""
        ts = [1.0, 1.05, 1.1]  # Three closely spaced packets
        shape_narrow = kde_shape(ts, duration=6.0, sigma=0.05,  t_sample=0.1)
        shape_wide   = kde_shape(ts, duration=6.0, sigma=1.0,   t_sample=0.1)
        # Wider kernel → lower peak value (energy is spread out more)
        assert np.max(shape_wide) < np.max(shape_narrow)
