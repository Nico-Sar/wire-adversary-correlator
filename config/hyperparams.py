"""
config/hyperparams.py
=====================
KDE preprocessing parameters and model hyperparameters.
All values are ShYSh baseline defaults unless noted.
"""

# ── KDE / Preprocessing ───────────────────────────────────────────────────────

KDE = {
    "sigma":      0.125,    # Gaussian kernel width (seconds)
    "t_sample":   0.1,      # Sampling period (seconds) → 10 samples/sec
    "window_len": 30,       # Window length in samples (= 3 seconds)
    "overlap":    0.5,      # Fractional overlap between windows
    "duration":   60.0,     # Max seconds of flow to analyze
    "min_packets": 5,       # Discard flows with fewer packets than this
}

# ── Model ─────────────────────────────────────────────────────────────────────

MODEL = {
    # CNN architecture (ShYSh-style dual-CNN)
    "conv1_filters":  32,
    "conv1_kernel":   8,
    "conv1_stride":   4,
    "conv2_filters":  64,
    "conv2_kernel":   8,
    "conv2_stride":   4,
    "fc_hidden":      128,

    # Training
    "batch_size":     64,
    "epochs":         100,
    "learning_rate":  1e-3,
    "weight_decay":   1e-4,
    "neg_pos_ratio":  10,   # Negative samples per positive (ShYSh: 10)
}

# ── Evaluation ────────────────────────────────────────────────────────────────

EVAL = {
    "train_split": 0.70,
    "val_split":   0.15,
    "test_split":  0.15,
    "base_rate":   1.9e-4,  # ShYSh reference base rate for PR-AUC context
}
