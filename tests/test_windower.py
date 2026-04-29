"""
tests/test_windower.py
======================
Unit tests for the sliding window slicer and time-window carving.

Run with:
  pytest tests/test_windower.py -v
"""

import numpy as np
import pytest

from preprocessing.windower import carve_time_window, slice_windows


class TestSliceWindows:

    def test_output_shape(self):
        """Basic shape check: (n_windows, window_len)."""
        signal = np.ones(100, dtype=np.float32)
        windows = slice_windows(signal, window_len=30, overlap=0.5)
        assert windows.ndim == 2
        assert windows.shape[1] == 30

    def test_window_count_no_overlap(self):
        """With 0% overlap, windows should tile exactly."""
        signal = np.ones(90, dtype=np.float32)
        windows = slice_windows(signal, window_len=30, overlap=0.0)
        assert windows.shape[0] == 3

    def test_window_count_50pct_overlap(self):
        """With 50% overlap: step = 15, signal = 60 → 3 windows."""
        signal = np.ones(60, dtype=np.float32)
        windows = slice_windows(signal, window_len=30, overlap=0.5)
        # starts: 0, 15, 30  (45 would give 45:75 which exceeds 60)
        assert windows.shape[0] == 3

    def test_content_preserved(self):
        """Windows should contain the correct signal values."""
        signal = np.arange(60, dtype=np.float32)
        windows = slice_windows(signal, window_len=10, overlap=0.0)
        np.testing.assert_array_equal(windows[0], signal[0:10])
        np.testing.assert_array_equal(windows[1], signal[10:20])

    def test_output_dtype_float32(self):
        signal = np.ones(60, dtype=np.float64)
        windows = slice_windows(signal, window_len=30, overlap=0.5)
        assert windows.dtype == np.float32

    def test_single_window(self):
        """Signal exactly the length of one window."""
        signal = np.ones(30, dtype=np.float32)
        windows = slice_windows(signal, window_len=30, overlap=0.5)
        assert windows.shape[0] == 1

    def test_signal_shorter_than_window(self):
        """Signal shorter than window_len — should return 0 windows."""
        signal = np.ones(10, dtype=np.float32)
        windows = slice_windows(signal, window_len=30, overlap=0.5)
        assert windows.shape[0] == 0

    def test_tail_zero_padded(self):
        """Tail is zero-padded so the last window is always full (ShYSh behaviour).

        signal=65, window=30, step=15: tail=(65-30)%15=5, pad=10 → 75 samples → 4 windows.
        The 4th window covers signal[45:75] = 20 real samples + 10 zeros.
        """
        signal = np.ones(65, dtype=np.float32)
        windows = slice_windows(signal, window_len=30, overlap=0.5)
        assert windows.shape[0] == 4
        np.testing.assert_array_equal(windows[3, :20], np.ones(20, dtype=np.float32))
        np.testing.assert_array_equal(windows[3, 20:], np.zeros(10, dtype=np.float32))

    def test_exact_fit_unchanged(self):
        """Signal whose length already satisfies (n-window)%step==0 is not padded."""
        # 300 samples = baseline mode KDE grid: (300-30)%15=0, no padding expected.
        signal = np.ones(300, dtype=np.float32)
        windows = slice_windows(signal, window_len=30, overlap=0.5)
        assert windows.shape[0] == 19
        # All windows must be all-ones (no padding artefacts).
        np.testing.assert_array_equal(windows, np.ones((19, 30), dtype=np.float32))


class TestCarveTimeWindow:

    def _make_packets(self, timestamps):
        return [{"ts": t, "size": 64, "direction": 1} for t in timestamps]

    def test_basic_carving(self):
        """Only packets within [t_start, t_end] should be returned."""
        packets = self._make_packets([9.9, 10.0, 10.5, 11.0, 11.1])
        result = carve_time_window(packets, t_start=10.0, t_end=11.0)
        assert len(result) == 3

    def test_timestamps_normalized_to_zero(self):
        """After carving, timestamps should be relative to t_start."""
        packets = self._make_packets([10.0, 10.5, 11.0])
        result = carve_time_window(packets, t_start=10.0, t_end=11.0)
        assert result[0]["ts"] == pytest.approx(0.0)
        assert result[1]["ts"] == pytest.approx(0.5)
        assert result[2]["ts"] == pytest.approx(1.0)

    def test_empty_window(self):
        """No packets in window → empty list."""
        packets = self._make_packets([1.0, 2.0, 3.0])
        result = carve_time_window(packets, t_start=10.0, t_end=20.0)
        assert result == []

    def test_other_fields_preserved(self):
        """size and direction should be unchanged after carving."""
        packets = [{"ts": 10.5, "size": 1500, "direction": -1}]
        result = carve_time_window(packets, t_start=10.0, t_end=11.0)
        assert result[0]["size"] == 1500
        assert result[0]["direction"] == -1
