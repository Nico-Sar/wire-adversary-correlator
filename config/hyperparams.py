"""
config/hyperparams.py
=====================
KDE preprocessing parameters and model hyperparameters.
All values are ShYSh baseline defaults unless noted.
"""
# ── Visit-Timeouts ───────────────────────────────────────────────────────


VISIT_TIMEOUTS = {
    "baseline": {"browser_ms": 30_000,  "curl_s": 60},
    "tor":      {"browser_ms": 120_000, "curl_s": 300},
    "vpn":      {"browser_ms": 60_000,  "curl_s": 120},
    "nym5":     {"browser_ms": 180_000, "curl_s": 600},
    # nym2 (2-hop) has fewer mix nodes than nym5 — tune after pilot
    "nym2":     {"browser_ms": 120_000, "curl_s": 360},
}

# ── KDE / Preprocessing ───────────────────────────────────────────────────────

KDE = {
    "sigma":       0.125,   # Gaussian kernel width (seconds)
    "t_sample":    0.1,     # Sampling period (seconds) → 10 samples/sec
    "window_len":  30,      # Window length in samples (= 3 seconds)
    "overlap":     0.5,     # Fractional overlap between windows
    "min_packets": 5,       # Discard flows with fewer packets than this
}

# Per-mode KDE overrides — duration and sigma vary with anonymity system latency
KDE_PER_MODE = {
    "baseline": {"duration": 30.0,  "sigma": 0.125},
    "tor":      {"duration": 60.0,  "sigma": 0.25},
    "vpn":      {"duration": 30.0,  "sigma": 0.125},
    "nym5":     {"duration": 60.0,  "sigma": 0.5},
    # nym2 (2-hop WireGuard): confirmed on pilot (56/59 visits built, span p95=9.2s, max=9.8s).
    # duration=30s: p95_span(9.2s)+10s buffer → 30s floor. n_windows=19 (same as baseline/vpn).
    # sigma=0.2: WireGuard UDP is high-density (~16k pkts/6s); inter-packet gap (~0.001s) is
    # meaningless for kernel width. 0.2s smooths the burst envelope without collapsing structure.
    "nym2":     {"duration": 30.0,  "sigma": 0.2},
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
    "min_visits_per_url": 2,  # Hard negatives require ≥2 visits per URL
}

# ── Collection behaviour ──────────────────────────────────────────────────────

COLLECTION = {
    # Rotate Tor circuit (NEWNYM) or Nym gateway before every visit.
    # Pass --rotate-circuits to coordinator.py to activate at runtime.
    # Recommended: True for all anonymity modes in the final collection run.
    "rotate_circuits": True,

    # Seconds to wait after NEWNYM before starting captures (Tor only).
    # Built into rotate_circuit_tor(); listed here for documentation.
    "tor_newnym_wait_s": 5,
}

# ── Evaluation ────────────────────────────────────────────────────────────────

EVAL = {
    "train_split": 0.70,
    "val_split":   0.15,
    "test_split":  0.15,
    "base_rate":   1.9e-4,  # ShYSh reference base rate for PR-AUC context
}

