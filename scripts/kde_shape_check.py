#!/usr/bin/env python3
"""
kde_shape_check.py — verify per-mode KDE shape signals aren't degenerate.

Run AFTER dataset_builder.py produces per-mode .npz files:
    python3 kde_shape_check.py data/campaign/datasets figures/kde_shapes

Expects {mode}_round01.npz with X_ingress_down, X_egress_down etc.
(the windowed (N, n_windows, window_len) format from dataset_builder).

Checks, per mode:
  1. per-window "energy" (sum of density per window) across ALL flows,
     to catch windows that are near-empty (degenerate signal).
  2. fraction of flows where >70% of windows are near-empty — if this
     fraction is high, the mode's shape signals carry little info for
     the correlator to learn from, regardless of what a single sample
     flow's plot looks like.
  3. a sample flow's flattened shape signal, for visual sanity check.

A mode is flagged DEGENERATE if the nonzero-window fraction across all
flows drops below 0.3 (i.e. more than 70% of windows in the dataset
carry essentially no density).
"""
import sys
import os
import glob
import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ENERGY_EPS = 1e-6
DEGENERATE_THRESHOLD = 0.3  # nonzero-window fraction below this = flagged


def analyze_npz(npz_path):
    d = np.load(npz_path, allow_pickle=True)
    keys = list(d.keys())

    # Prefer the downstream signal (the correlation-bearing direction per ShYSh);
    # fall back to whatever windowed array is present.
    key = None
    for cand in ("X_ingress_down", "X_egress_down", "X_ingress_up", "X_egress_up"):
        if cand in keys:
            key = cand
            break
    if key is None:
        # last resort: first array that looks 3D
        for k in keys:
            if d[k].ndim == 3:
                key = k
                break
    if key is None:
        return None, keys

    X = d[key]  # (N, n_windows, window_len)
    if X.shape[0] == 0:
        return {"n_flows": 0, "key": key}, keys

    energy = X.sum(axis=2)                       # (N, n_windows)
    window_nonzero = energy > ENERGY_EPS          # (N, n_windows)

    nonzero_frac_global = window_nonzero.mean()   # across all flows & windows
    # per-flow fraction of nonzero windows, then how many flows are mostly-empty
    per_flow_nonzero_frac = window_nonzero.mean(axis=1)   # (N,)
    frac_flows_mostly_empty = (per_flow_nonzero_frac < DEGENERATE_THRESHOLD).mean()

    return {
        "n_flows": X.shape[0],
        "n_windows": X.shape[1],
        "window_len": X.shape[2],
        "key": key,
        "nonzero_frac_global": nonzero_frac_global,
        "frac_flows_mostly_empty": frac_flows_mostly_empty,
        "sample_flat": X[0].reshape(-1),
        "energy_flat": energy.reshape(-1),
        "degenerate": nonzero_frac_global < DEGENERATE_THRESHOLD,
    }, keys


def main(npz_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    npzs = sorted(glob.glob(f"{npz_dir}/*.npz"))
    if not npzs:
        print(f"No .npz files found in {npz_dir}")
        return

    fig, axes = plt.subplots(len(npzs), 2, figsize=(14, 3.2 * len(npzs)))
    if len(npzs) == 1:
        axes = axes.reshape(1, -1)

    print(f"{'mode':<12} {'n_flows':>8} {'nonzero_frac':>13} {'mostly_empty_flows':>19}  verdict")
    print("-" * 75)

    for row, npz in enumerate(npzs):
        mode = os.path.basename(npz).replace(".npz", "").replace("_round01", "")
        result, keys = analyze_npz(npz)

        if result is None:
            print(f"{mode:<12}  -- no 3D array found; keys were {keys}")
            axes[row, 0].set_title(f"{mode}: no usable array ({keys})")
            axes[row, 1].axis("off")
            continue

        if result.get("n_flows", 0) == 0:
            print(f"{mode:<12}  -- EMPTY dataset")
            axes[row, 0].set_title(f"{mode}: EMPTY")
            axes[row, 1].axis("off")
            continue

        verdict = "DEGENERATE!" if result["degenerate"] else "ok"
        print(f"{mode:<12} {result['n_flows']:>8} {result['nonzero_frac_global']:>13.3f} "
              f"{result['frac_flows_mostly_empty']:>19.3f}  {verdict}")

        axes[row, 0].plot(result["sample_flat"], color="#dc2626")
        axes[row, 0].set_title(f"{mode}: sample flow shape ({result['key']}, flattened windows)")
        axes[row, 0].set_ylabel("density")

        axes[row, 1].hist(result["energy_flat"], bins=50, color="#2563eb", alpha=0.8)
        axes[row, 1].set_title(
            f"{mode}: per-window energy (nonzero frac={result['nonzero_frac_global']:.2f}) "
            f"{'<-- ' + verdict if result['degenerate'] else ''}"
        )
        axes[row, 1].set_xlabel("window total density")

    plt.tight_layout()
    out_path = f"{out_dir}/kde_shapes_per_mode.png"
    plt.savefig(out_path, dpi=130)
    plt.close()
    print(f"\nWritten: {out_path}")
    print(f"(pull this to lex to actually view it, since leroy is headless)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <npz_dir> <out_dir>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
