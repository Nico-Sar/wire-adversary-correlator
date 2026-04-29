#!/usr/bin/env python3
"""
scripts/analyze_quick_test.py
==============================
Full preprocessing + analysis pipeline for data/quick_test/ across all 5 modes.

Reports:
  1. Visits built vs skipped per mode with skip reasons
  2. Per-stream mean packet counts (ingress_up/down, egress_up/down)
  3. Flow duration statistics (mean, p95, max) per mode
  4. KDE normalisation check: sum(shape) == n_grid_samples
  5. n_windows per mode
  6. KDE plots for sample flow pairs per mode → data/quick_test/kde_plots/
  7. check_pilot_npz.py on all output NPZ files
  8. Per-mode per-file-type build rate table
  9. nym2 ingress packet counts — confirm non-zero

Usage (from repo root):
    python3 scripts/analyze_quick_test.py
"""

import json
import logging
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

# Ensure repo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from config.hyperparams import KDE, KDE_PER_MODE
from config.infrastructure import get_client_private_ip
from preprocessing.kde import kde_shape, split_directions
from preprocessing.pcap_parser import extract_packets
from preprocessing.windower import carve_time_window, slice_windows
from preprocessing.quartet_builder import compute_quartet

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Mode configuration ─────────────────────────────────────────────────────────

DATA_ROOT   = Path("data/quick_test")
OUTPUT_ROOT = DATA_ROOT
PLOT_DIR    = DATA_ROOT / "kde_plots"

MODES = {
    "baseline": {
        "labels":   DATA_ROOT / "baseline_visits.jsonl",
        "data_dir": DATA_ROOT / "baseline",
        "output":   OUTPUT_ROOT / "baseline_dataset.npz",
    },
    "vpn": {
        "labels":   DATA_ROOT / "vpn_visits.jsonl",
        "data_dir": DATA_ROOT / "vpn",
        "output":   OUTPUT_ROOT / "vpn_dataset.npz",
    },
    "tor": {
        "labels":   DATA_ROOT / "tor_visits.jsonl",
        "data_dir": DATA_ROOT / "tor",
        "output":   OUTPUT_ROOT / "tor_dataset.npz",
    },
    "nym5": {
        "labels":   DATA_ROOT / "nym5_visits.jsonl",
        "data_dir": DATA_ROOT / "nym5",
        "output":   OUTPUT_ROOT / "nym5_dataset.npz",
    },
    "nym2": {
        "labels":   DATA_ROOT / "nym2_visits.jsonl",
        "data_dir": DATA_ROOT / "nym2",
        "output":   OUTPUT_ROOT / "nym2_dataset.npz",
    },
}

# ── KDE normalisation probe ────────────────────────────────────────────────────

def check_kde_norm(timestamps, duration, sigma, t_sample):
    """Returns (sum_val, n_samples, passes) for one KDE shape."""
    n_samples = int(np.ceil(duration / t_sample))
    shape = kde_shape(timestamps, duration=duration, sigma=sigma, t_sample=t_sample)
    s = float(shape.sum())
    passes = abs(s - n_samples) < 1e-3 or len(timestamps) == 0
    return s, n_samples, passes


# ── Per-mode instrumented build ────────────────────────────────────────────────

def build_mode(mode: str, cfg: dict) -> dict:
    """
    Instrumented version of dataset_builder.build_dataset().
    Returns a stats dict with everything needed for all report items.
    """
    labels_jsonl = cfg["labels"]
    data_dir     = Path(cfg["data_dir"])
    output_path  = cfg["output"]

    mode_kde = KDE_PER_MODE[mode]
    duration = mode_kde["duration"]
    sigma    = mode_kde["sigma"]
    t_sample = KDE["t_sample"]
    min_pkts = KDE["min_packets"]

    stats = {
        "mode":         mode,
        "total_jsonl":  0,
        "failed_jsonl": 0,   # visit_status != success
        "skipped":      0,
        "built":        0,
        "skip_reasons": defaultdict(int),
        "stream_pkts":  defaultdict(list),   # stream → [pkt_count, ...]
        "durations":    [],                  # t_end - t_start (s) per visit
        "kde_norm_ok":  True,
        "kde_norm_samples": [],              # (sum_val, n_samples) per visit
        "n_windows":    None,
        "window_len":   None,
        # raw lists for NPZ building
        "_ingress_up_list":   [],
        "_ingress_down_list": [],
        "_egress_up_list":    [],
        "_egress_down_list":  [],
        "_visit_ids":         [],
        "_urls":              [],
        # per-file type counts for build-rate table
        "file_type_counts": defaultdict(lambda: {"present": 0, "missing": 0}),
    }

    # ── Read JSONL ────────────────────────────────────────────────────────────
    records = []
    with open(labels_jsonl) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            stats["total_jsonl"] += 1
            if rec.get("visit_status") != "success":
                stats["failed_jsonl"] += 1
                continue
            if rec.get("mode") != mode:
                continue
            records.append(rec)

    log.info(f"[{mode}] {len(records)} successful JSONL records")

    for i, rec in enumerate(records):
        visit_id  = rec["visit_id"]
        url       = rec["url"]
        client_id = visit_id.split("_v")[0]
        t_start   = rec["t_visit_start"]
        t_end     = rec["t_visit_end"]
        duration_visit = t_end - t_start
        stats["durations"].append(duration_visit)

        ingress_pcap = data_dir / f"{visit_id}_ingress.pcap"
        egress_pcap  = data_dir / f"{visit_id}_egress.pcap"

        # Track file presence for build-rate table
        for ftype in ("ingress", "egress"):
            p = data_dir / f"{visit_id}_{ftype}.pcap"
            if p.exists():
                stats["file_type_counts"][ftype]["present"] += 1
            else:
                stats["file_type_counts"][ftype]["missing"] += 1

        if not ingress_pcap.exists() or not egress_pcap.exists():
            stats["skipped"] += 1
            stats["skip_reasons"]["missing_pcap"] += 1
            log.warning(f"  [{i+1}] {visit_id}: missing pcap — skipping")
            continue

        try:
            client_ip = get_client_private_ip(client_id)
        except KeyError:
            stats["skipped"] += 1
            stats["skip_reasons"]["unknown_client_id"] += 1
            log.warning(f"  [{i+1}] {visit_id}: unknown client_id '{client_id}' — skipping")
            continue

        try:
            quartet = compute_quartet(
                ingress_pcap=str(ingress_pcap),
                egress_pcap=str(egress_pcap),
                t_start=t_start,
                t_end=t_end,
                client_private_ip=client_ip,
                mode=mode,
            )
        except Exception as e:
            stats["skipped"] += 1
            stats["skip_reasons"]["quartet_failed"] += 1
            log.warning(f"  [{i+1}] {visit_id}: quartet failed: {e} — skipping")
            continue

        stream_counts = {
            "ingress_up":   quartet["n_ingress_up"],
            "ingress_down": quartet["n_ingress_down"],
            "egress_up":    quartet["n_egress_up"],
            "egress_down":  quartet["n_egress_down"],
        }
        low_streams = [k for k, v in stream_counts.items() if v < min_pkts]
        if low_streams:
            stats["skipped"] += 1
            stats["skip_reasons"]["low_packet_count"] += 1
            log.warning(f"  [{i+1}] {visit_id}: low stream pkts {low_streams} {stream_counts} — skipping")
            continue

        zero_streams = [
            k for k in ("ingress_up", "ingress_down", "egress_up", "egress_down")
            if quartet[k].shape[0] == 0
        ]
        if zero_streams:
            stats["skipped"] += 1
            stats["skip_reasons"]["zero_windows"] += 1
            log.warning(f"  [{i+1}] {visit_id}: zero windows in {zero_streams} — skipping")
            continue

        # Accumulate per-stream packet counts
        for stream in ("ingress_up", "ingress_down", "egress_up", "egress_down"):
            stats["stream_pkts"][stream].append(stream_counts[stream])

        # KDE normalisation probe on ingress_up (representative)
        # Re-parse the ingress pcap to get raw timestamps for the norm check
        try:
            ingress_pkts = extract_packets(str(ingress_pcap), local_ip=client_ip)
            ingress_carved = carve_time_window(ingress_pkts, t_start - 0.5, t_end + 3.0)
            up_ts, _ = split_directions(ingress_carved)
            s_val, n_samp, passes = check_kde_norm(up_ts, duration=duration,
                                                    sigma=sigma, t_sample=t_sample)
            stats["kde_norm_samples"].append((s_val, n_samp, passes))
            if not passes:
                stats["kde_norm_ok"] = False
                log.warning(f"  [{i+1}] {visit_id}: KDE norm FAIL sum={s_val:.3f} expected={n_samp}")
        except Exception as e:
            log.warning(f"  [{i+1}] {visit_id}: KDE norm check error: {e}")

        stats["_ingress_up_list"].append(quartet["ingress_up"])
        stats["_ingress_down_list"].append(quartet["ingress_down"])
        stats["_egress_up_list"].append(quartet["egress_up"])
        stats["_egress_down_list"].append(quartet["egress_down"])
        stats["_visit_ids"].append(visit_id)
        stats["_urls"].append(url)
        stats["built"] += 1

    # ── Stack and save NPZ ────────────────────────────────────────────────────
    N = stats["built"]
    if N == 0:
        log.error(f"[{mode}] No valid visits — cannot build NPZ")
        stats["npz_path"] = None
        return stats

    n_windows_all = [a.shape[0] for a in stats["_ingress_up_list"]]
    if len(set(n_windows_all)) != 1:
        log.error(f"[{mode}] Inconsistent window counts: {set(n_windows_all)}")
        stats["npz_path"] = None
        return stats

    stats["n_windows"]  = n_windows_all[0]
    stats["window_len"] = stats["_ingress_up_list"][0].shape[1]

    rng = np.random.default_rng(42)
    X_iu = np.stack(stats["_ingress_up_list"])
    X_id = np.stack(stats["_ingress_down_list"])
    X_eu = np.stack(stats["_egress_up_list"])
    X_ed = np.stack(stats["_egress_down_list"])
    visit_ids = np.array(stats["_visit_ids"])
    urls      = np.array(stats["_urls"])
    modes_arr = np.array([mode] * N)

    ing_order = rng.permutation(N)
    eg_order  = rng.permutation(N)

    X_iu = X_iu[ing_order]; X_id = X_id[ing_order]
    X_eu = X_eu[eg_order];  X_ed = X_ed[eg_order]
    ing_vids = visit_ids[ing_order]
    eg_vids  = visit_ids[eg_order]
    ing_urls = urls[ing_order]
    eg_urls  = urls[eg_order]

    ing_id2idx = {vid: idx for idx, vid in enumerate(ing_vids)}
    eg_id2idx  = {vid: idx for idx, vid in enumerate(eg_vids)}
    pairs = np.array(
        [[ing_id2idx[vid], eg_id2idx[vid], 1] for vid in stats["_visit_ids"]],
        dtype=np.int32,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        str(output_path),
        X_ingress_up=X_iu, X_ingress_down=X_id,
        X_egress_up=X_eu,  X_egress_down=X_ed,
        ingress_visit_ids=ing_vids, egress_visit_ids=eg_vids,
        ingress_urls=ing_urls, egress_urls=eg_urls,
        pairs=pairs,
        modes=modes_arr[ing_order],
    )
    size_mb = output_path.stat().st_size / 1e6
    log.info(f"[{mode}] Saved {output_path}  ({size_mb:.1f} MB)  shape={X_iu.shape}")
    stats["npz_path"] = output_path
    return stats


# ── KDE plot — per-mode sample pair ───────────────────────────────────────────

STREAM_COLORS = {
    "ingress_up":   "#1D8DB0",
    "ingress_down": "#2F4D5D",
    "egress_up":    "#F26B43",
    "egress_down":  "#6C3D91",
}
STREAM_LABELS = {
    "ingress_up":   "Ingress UP",
    "ingress_down": "Ingress DOWN",
    "egress_up":    "Egress UP",
    "egress_down":  "Egress DOWN",
}


def _stitch(windows):
    L    = windows.shape[1]
    step = max(1, L // 2)
    n_w  = windows.shape[0]
    length = step * (n_w - 1) + L
    signal = np.zeros(length, dtype=np.float32)
    count  = np.zeros(length, dtype=np.float32)
    for i, w in enumerate(windows):
        s = i * step
        signal[s:s + L] += w
        count[s:s + L]  += 1.0
    return signal / np.maximum(count, 1.0)


def plot_mode_sample(mode: str, npz_path: Path, out_dir: Path):
    """Plots all 4 KDE streams for the first flow pair in the NPZ."""
    data     = np.load(str(npz_path), allow_pickle=True)
    pairs    = data["pairs"]
    ing_idx, eg_idx, _ = pairs[0]

    streams = {
        "ingress_up":   _stitch(data["X_ingress_up"][ing_idx]),
        "ingress_down": _stitch(data["X_ingress_down"][ing_idx]),
        "egress_up":    _stitch(data["X_egress_up"][eg_idx]),
        "egress_down":  _stitch(data["X_egress_down"][eg_idx]),
    }

    t_sample = KDE["t_sample"]
    fig, axes = plt.subplots(2, 2, figsize=(12, 6))
    fig.suptitle(f"KDE shape — {mode}  (sample pair pair[0])", fontsize=13, fontweight="bold")

    for ax, (stream, signal) in zip(axes.flat, streams.items()):
        t = np.arange(len(signal)) * t_sample
        color = STREAM_COLORS[stream]
        ax.fill_between(t, signal, alpha=0.2, color=color)
        ax.plot(t, signal, color=color, linewidth=1.2)
        ax.set_title(STREAM_LABELS[stream], fontsize=10)
        ax.set_xlabel("Time (s)", fontsize=8)
        ax.set_ylabel("Packet density", fontsize=8)
        ax.spines[["top", "right"]].set_visible(False)

    plt.tight_layout()
    out_path = out_dir / f"{mode}_sample_pair.png"
    fig.savefig(str(out_path), dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  Saved: {out_path}")


# ── Section printer ────────────────────────────────────────────────────────────

def section(title):
    print(f"\n{'═'*64}")
    print(f"  {title}")
    print(f"{'═'*64}")


# ── check_pilot_npz runner ─────────────────────────────────────────────────────

def run_check_pilot_npz(npz_paths):
    import importlib.util, io, contextlib
    spec = importlib.util.spec_from_file_location(
        "check_pilot_npz",
        str(Path(__file__).parent / "check_pilot_npz.py"),
    )
    mod = importlib.util.load_from_spec = None  # noqa — use subprocess instead

    import subprocess
    for p in npz_paths:
        print(f"\n  Running check_pilot_npz.py on {p.name}")
        result = subprocess.run(
            [sys.executable, "scripts/check_pilot_npz.py", str(p)],
            capture_output=True, text=True,
        )
        # Indent output
        for line in (result.stdout + result.stderr).splitlines():
            print(f"    {line}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    PLOT_DIR.mkdir(parents=True, exist_ok=True)

    all_stats = {}
    for mode, cfg in MODES.items():
        log.info(f"\n{'─'*50}")
        log.info(f"  Processing mode: {mode}")
        log.info(f"{'─'*50}")
        all_stats[mode] = build_mode(mode, cfg)

    # ══════════════════════════════════════════════════════════════════════
    # 1. Visits built vs skipped per mode
    # ══════════════════════════════════════════════════════════════════════
    section("1. Visits built vs skipped per mode")
    print(f"\n  {'Mode':<12} {'Total':>7} {'Failed':>7} {'Skipped':>8} {'Built':>7}  Skip reasons")
    print(f"  {'─'*12} {'─'*7} {'─'*7} {'─'*8} {'─'*7}  {'─'*40}")
    for mode, s in all_stats.items():
        reasons_str = "  ".join(f"{k}={v}" for k, v in s["skip_reasons"].items()) or "—"
        print(f"  {mode:<12} {s['total_jsonl']:>7} {s['failed_jsonl']:>7} "
              f"{s['skipped']:>8} {s['built']:>7}  {reasons_str}")

    # ══════════════════════════════════════════════════════════════════════
    # 2. Per-stream mean packet counts
    # ══════════════════════════════════════════════════════════════════════
    section("2. Per-stream mean packet counts (built visits only)")
    print(f"\n  {'Mode':<12} {'ing_up':>10} {'ing_down':>10} {'eg_up':>10} {'eg_down':>10}")
    print(f"  {'─'*12} {'─'*10} {'─'*10} {'─'*10} {'─'*10}")
    for mode, s in all_stats.items():
        sp = s["stream_pkts"]
        def mu(k):
            lst = sp[k]
            return f"{np.mean(lst):.1f}" if lst else "—"
        print(f"  {mode:<12} {mu('ingress_up'):>10} {mu('ingress_down'):>10} "
              f"{mu('egress_up'):>10} {mu('egress_down'):>10}")

    # ══════════════════════════════════════════════════════════════════════
    # 3. Flow duration statistics
    # ══════════════════════════════════════════════════════════════════════
    section("3. Flow duration statistics (t_visit_end − t_visit_start, all successful records)")
    print(f"\n  {'Mode':<12} {'mean (s)':>10} {'p95 (s)':>10} {'max (s)':>10}")
    print(f"  {'─'*12} {'─'*10} {'─'*10} {'─'*10}")
    for mode, s in all_stats.items():
        d = s["durations"]
        if d:
            print(f"  {mode:<12} {np.mean(d):>10.2f} {np.percentile(d, 95):>10.2f} {np.max(d):>10.2f}")
        else:
            print(f"  {mode:<12} {'—':>10} {'—':>10} {'—':>10}")

    # ══════════════════════════════════════════════════════════════════════
    # 4. KDE normalisation check
    # ══════════════════════════════════════════════════════════════════════
    section("4. KDE normalisation check  (sum(shape) == n_grid_samples)")
    for mode, s in all_stats.items():
        norms = s["kde_norm_samples"]
        if not norms:
            print(f"  {mode:<12}  no samples")
            continue
        all_pass = all(p for _, _, p in norms)
        sum_vals  = [v for v, _, _ in norms]
        n_samp    = norms[0][1]
        status    = "PASS" if all_pass else "FAIL"
        print(f"  {mode:<12}  {status}  n_grid={n_samp}  "
              f"sum mean={np.mean(sum_vals):.3f}  "
              f"sum min={np.min(sum_vals):.3f}  "
              f"sum max={np.max(sum_vals):.3f}  "
              f"(checked {len(norms)} visits)")

    # ══════════════════════════════════════════════════════════════════════
    # 5. n_windows per mode
    # ══════════════════════════════════════════════════════════════════════
    section("5. n_windows per mode")
    print(f"\n  {'Mode':<12} {'n_windows':>10} {'window_len':>12} {'duration (s)':>14} {'t_sample':>10}")
    print(f"  {'─'*12} {'─'*10} {'─'*12} {'─'*14} {'─'*10}")
    for mode, s in all_stats.items():
        mk = KDE_PER_MODE[mode]
        nw = s["n_windows"] if s["n_windows"] is not None else "—"
        wl = s["window_len"] if s["window_len"] is not None else "—"
        print(f"  {mode:<12} {str(nw):>10} {str(wl):>12} {mk['duration']:>14.1f} {KDE['t_sample']:>10.1f}")

    # ══════════════════════════════════════════════════════════════════════
    # 6. KDE plots
    # ══════════════════════════════════════════════════════════════════════
    section(f"6. KDE plots → {PLOT_DIR}/")
    for mode, s in all_stats.items():
        if s.get("npz_path") and s["npz_path"].exists():
            try:
                plot_mode_sample(mode, s["npz_path"], PLOT_DIR)
            except Exception as e:
                print(f"  [WARN] {mode}: plot failed: {e}")
        else:
            print(f"  [{mode}] no NPZ available — skipping plot")

    # Cross-mode comparison via plot_kde_shapes.py (all 5 modes)
    npz_available = {m: all_stats[m]["npz_path"] for m in MODES
                     if all_stats[m].get("npz_path") and all_stats[m]["npz_path"].exists()}
    if len(npz_available) >= 3:
        import subprocess
        cmd = [
            sys.executable, "scripts/plot_kde_shapes.py",
            "--baseline", str(npz_available.get("baseline", "")),
            "--tor",      str(npz_available.get("tor",  "")),
            "--vpn",      str(npz_available.get("vpn",  "")),
            "--output",   str(PLOT_DIR / "cross_mode_comparison.png"),
        ]
        if "nym5" in npz_available:
            cmd += ["--nym5", str(npz_available["nym5"])]
        if "nym2" in npz_available:
            cmd += ["--nym2", str(npz_available["nym2"])]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"  Saved: {PLOT_DIR}/cross_mode_comparison.png")
        else:
            print(f"  [WARN] cross-mode plot failed: {result.stderr.strip()}")

    # ══════════════════════════════════════════════════════════════════════
    # 7. check_pilot_npz.py
    # ══════════════════════════════════════════════════════════════════════
    section("7. check_pilot_npz.py on all NPZ files")
    npz_paths = [s["npz_path"] for s in all_stats.values()
                 if s.get("npz_path") and s["npz_path"].exists()]
    if npz_paths:
        run_check_pilot_npz(npz_paths)
    else:
        print("  No NPZ files to check.")

    # ══════════════════════════════════════════════════════════════════════
    # 8. Per-mode per-file-type build rate table
    # ══════════════════════════════════════════════════════════════════════
    section("8. Per-mode per-file-type build rate")
    print(f"\n  {'Mode':<12} {'File type':<12} {'Present':>9} {'Missing':>9} {'Rate':>8}")
    print(f"  {'─'*12} {'─'*12} {'─'*9} {'─'*9} {'─'*8}")
    for mode, s in all_stats.items():
        for ftype in ("ingress", "egress"):
            c = s["file_type_counts"][ftype]
            total = c["present"] + c["missing"]
            rate  = f"{100*c['present']/max(total,1):.1f}%" if total else "—"
            print(f"  {mode:<12} {ftype:<12} {c['present']:>9} {c['missing']:>9} {rate:>8}")

    # ══════════════════════════════════════════════════════════════════════
    # 9. nym2 ingress packet counts — confirm non-zero
    # ══════════════════════════════════════════════════════════════════════
    section("9. nym2 ingress packet counts (raw pcap, all successful visits)")
    nym2_cfg = MODES["nym2"]
    nym2_data_dir = Path(nym2_cfg["data_dir"])
    nym2_labels   = nym2_cfg["labels"]

    print(f"\n  {'Visit ID':<30} {'client_ip':<14} {'n_ingress_pkts':>14} {'n_ingress_up':>13} {'n_ingress_down':>14}")
    print(f"  {'─'*30} {'─'*14} {'─'*14} {'─'*13} {'─'*14}")

    zero_ingress = []
    with open(nym2_labels) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("visit_status") != "success":
                continue
            visit_id  = rec["visit_id"]
            client_id = visit_id.split("_v")[0]
            t_start   = rec["t_visit_start"]
            t_end     = rec["t_visit_end"]

            ingress_pcap = nym2_data_dir / f"{visit_id}_ingress.pcap"
            if not ingress_pcap.exists():
                print(f"  {visit_id:<30} {'—':<14} {'MISSING':>14}")
                continue

            try:
                client_ip = get_client_private_ip(client_id)
            except KeyError:
                print(f"  {visit_id:<30} {'unknown':<14} {'KEY_ERR':>14}")
                continue

            pkts = extract_packets(str(ingress_pcap), local_ip=client_ip)
            carved = carve_time_window(pkts, t_start - 0.5, t_end + 3.0)
            up_ts, down_ts = split_directions(carved)
            n_total = len(carved)
            n_up    = len(up_ts)
            n_down  = len(down_ts)

            status = ""
            if n_total == 0:
                status = " ← ZERO!"
                zero_ingress.append(visit_id)

            print(f"  {visit_id:<30} {client_ip:<14} {n_total:>14} {n_up:>13} {n_down:>14}{status}")

    if zero_ingress:
        print(f"\n  WARNING: {len(zero_ingress)} visit(s) had ZERO ingress packets: {zero_ingress}")
    else:
        print(f"\n  All nym2 ingress captures are non-zero.")

    section("Done")
    print(f"  NPZ files: {[str(s['npz_path']) for s in all_stats.values() if s.get('npz_path')]}")
    print(f"  Plots:     {PLOT_DIR}/")
    print()


if __name__ == "__main__":
    main()
