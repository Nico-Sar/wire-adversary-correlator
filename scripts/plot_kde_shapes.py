"""
scripts/plot_kde_shapes.py
==========================
Generates a KDE shape comparison figure from pilot .npz datasets.

Shows ingress_down and egress_down KDE density waves for the same URL
visited via Baseline, Tor, VPN, Nym-5hop, and Nym-2hop — illustrating how
each anonymity system distorts the traffic shape at the wire level.

Rows:
  0 — Ingress downstream (KDE density)
  1 — Egress  downstream (KDE density)

Usage (from repo root):
    python3 scripts/plot_kde_shapes.py \
        --baseline  data/pilot/baseline_dataset.npz \
        --tor       data/pilot/tor_dataset.npz \
        --vpn       data/pilot/vpn_dataset.npz \
        --nym5      data/pilot/nym5_dataset.npz \
        --nym2      data/pilot/nym2_dataset.npz \
        --url       "page_html_1.html" \
        --output    figures/kde_shape_comparison.png

The script picks the first visit for the given URL in each dataset.
If --url is omitted it uses the first URL alphabetically.
--nym5 and --nym2 are optional; columns are omitted if not provided.
"""

import argparse
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

# ── KUL colour scheme ──────────────────────────────────────────────────────
KUL_TEAL  = "#1D8DB0"
KUL_NAVY  = "#2F4D5D"
KUL_LBLUE = "#DCE7F0"
KUL_ORANGE= "#F26B43"

MODE_COLORS = {
    "Baseline": KUL_NAVY,
    "Tor":      KUL_TEAL,
    "VPN":      KUL_ORANGE,
    "Nym-5hop": "#6C3D91",
    "Nym-2hop": "#A855B5",
}

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config.hyperparams import KDE, KDE_PER_MODE as _KDE_PER_MODE

# Build plot params from canonical config so this script never goes stale.
KDE_PER_MODE = {
    mode: {**_KDE_PER_MODE.get(mode, {}), "t_sample": KDE["t_sample"]}
    for mode in ("baseline", "tor", "vpn", "nym5", "nym2")
}


# ── Data loading ───────────────────────────────────────────────────────────

def load_first_visit(npz_path: str, url_filter: str | None = None):
    """
    Returns (ingress_down, egress_down, url, mode) for the first visit
    matching url_filter in the .npz file. Both arrays are 1D signals
    reconstructed by stitching overlapping windows back together.
    """
    data = np.load(npz_path, allow_pickle=True)

    if "ingress_urls" in data:
        urls  = list(data["ingress_urls"])
        mode  = str(data["modes"][0])
        pairs = data["pairs"]           # (N, 3) — [ing_idx, eg_idx, label]
    else:
        urls  = list(data["urls"])
        mode  = "baseline"
        pairs = np.array([[i, i, 1] for i in range(len(urls))], dtype=np.int32)

    unique_urls = sorted(set(urls))
    if url_filter:
        candidates = [u for u in unique_urls if url_filter in u]
        target_url = candidates[0] if candidates else unique_urls[0]
    else:
        target_url = unique_urls[0]

    def stitch(windows):
        L      = windows.shape[1]
        step   = L // 2
        n_w    = windows.shape[0]
        length = step * (n_w - 1) + L
        signal = np.zeros(length, dtype=np.float32)
        count  = np.zeros(length, dtype=np.float32)
        for i, w in enumerate(windows):
            start = i * step
            signal[start:start + L] += w
            count[start:start + L]  += 1.0
        return signal / np.maximum(count, 1.0)

    for ing_idx, eg_idx, _ in pairs:
        if urls[ing_idx] == target_url:
            ing_down = stitch(data["X_ingress_down"][ing_idx])
            eg_down  = stitch(data["X_egress_down"][eg_idx])
            return ing_down, eg_down, target_url, mode

    raise ValueError(f"No visit found for URL containing '{url_filter}' in {npz_path}")


def make_time_axis(signal_len: int, t_sample: float = 0.1) -> np.ndarray:
    return np.arange(signal_len) * t_sample


# ── Plotting ───────────────────────────────────────────────────────────────

def plot_comparison(datasets: dict, url_filter: str | None, output: str):
    """
    datasets : {"Baseline": npz_path, "Tor": npz_path, ...}
    """
    n_modes = len(datasets)

    fig = plt.figure(figsize=(17, 7))
    fig.patch.set_facecolor("white")

    gs = gridspec.GridSpec(
        2, n_modes,
        figure=fig,
        hspace=0.55,
        wspace=0.35,
        left=0.08, right=0.97,
        top=0.88,  bottom=0.10,
    )

    row_labels = ["Ingress — downstream", "Egress — downstream"]

    visited_url = None
    results = {}

    for label, path in datasets.items():
        ing_down, eg_down, url, mode = load_first_visit(path, url_filter)
        visited_url = url
        results[label] = {"ing_down": ing_down, "eg_down": eg_down, "mode": mode}

    for col_i, label in enumerate(results.keys()):
        res    = results[label]
        mode_k = res["mode"]
        color  = MODE_COLORS[label]
        params = KDE_PER_MODE[mode_k]
        t_s    = params["t_sample"]
        dur    = params["duration"]
        sigma  = params["sigma"]

        for row_i, key in enumerate(["ing_down", "eg_down"]):
            ax = fig.add_subplot(gs[row_i, col_i])
            signal = res[key]
            t      = make_time_axis(len(signal), t_s)
            mask   = t <= dur
            t_plot = t[mask]
            s_plot = signal[mask]

            ax.fill_between(t_plot, s_plot, alpha=0.18, color=color)
            ax.plot(t_plot, s_plot, color=color, linewidth=1.1, alpha=0.9)

            ax.set_xlim(0, dur)
            ax.set_ylim(bottom=0)
            _style_ax(ax)
            ax.set_xlabel("Time (s)", fontsize=8, color="#555555")

            if col_i == 0:
                ax.set_ylabel("Packet density", fontsize=8, color="#555555")
                _row_label(ax, row_labels[row_i])

            if row_i == 0:
                ax.set_title(label, fontsize=13, fontweight="bold",
                             color=color, pad=8)

            ax.annotate(f"σ = {sigma} s", xy=(0.97, 0.93),
                        xycoords="axes fraction", fontsize=7.5,
                        color=color, ha="right", va="top")

    # Figure title
    short_url = Path(visited_url).name if visited_url else "unknown"
    fig.suptitle(
        f"KDE traffic shape: ingress vs egress downstream — {short_url}",
        fontsize=13, fontweight="bold", color=KUL_NAVY, y=0.96,
    )

    # Bottom caption
    fig.text(
        0.5, 0.02,
        "Gaussian KDE · T = 0.1 s/sample · σ tuned per mode  |  "
        "Pilot collection — Hetzner Cloud HEL1",
        ha="center", fontsize=7.5, color="#888888", style="italic",
    )

    Path(output).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=180, bbox_inches="tight", facecolor="white")
    print(f"Saved: {output}")
    plt.close(fig)


def _style_ax(ax):
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines["left"].set_color("#CCCCCC")
    ax.spines["bottom"].set_color("#CCCCCC")
    ax.tick_params(labelsize=8, colors="#555555")


def _row_label(ax, text):
    ax.annotate(
        text,
        xy=(-0.22, 0.5),
        xycoords="axes fraction",
        fontsize=9, fontweight="bold", color=KUL_NAVY,
        rotation=90, va="center", ha="center",
    )


# ── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Plot KDE shape comparison from pilot .npz datasets"
    )
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--tor",      required=True)
    parser.add_argument("--vpn",      required=True)
    parser.add_argument("--nym5",     default=None,
                        help="Nym 5-hop .npz dataset (column omitted if not provided)")
    parser.add_argument("--nym2",     default=None,
                        help="Nym 2-hop .npz dataset (column omitted if not provided)")
    parser.add_argument("--url",      default=None,
                        help="Partial URL string to match (e.g. 'page_html_1')")
    parser.add_argument("--output",   default="figures/kde_shape_comparison.png")
    args = parser.parse_args()

    datasets = {
        "Baseline": args.baseline,
        "Tor":      args.tor,
        "VPN":      args.vpn,
    }
    if args.nym5:
        datasets["Nym-5hop"] = args.nym5
    if args.nym2:
        datasets["Nym-2hop"] = args.nym2

    plot_comparison(datasets, args.url, args.output)
