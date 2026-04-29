"""
scripts/analyze_pilot_flows.py
==============================
Reads pilot JSONL logs and ingress pcaps to report per-mode flow statistics.
Used to calibrate sigma and duration KDE parameters.

Usage (from repo root):
    python3 scripts/analyze_pilot_flows.py
"""

import json
import sys
from pathlib import Path

import numpy as np

# Run from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.infrastructure import CLIENTS
from preprocessing.pcap_parser import extract_packets
from preprocessing.windower import carve_time_window

MODES = ["baseline", "vpn", "tor", "nym5", "nym2"]
PILOT_DIR = Path("data/pilot")

# Current hyperparams for reference
CURRENT_PARAMS = {
    "baseline": {"sigma": 0.125, "duration": 30.0},
    "vpn":      {"sigma": 0.125, "duration": 30.0},
    "tor":      {"sigma": 0.25,  "duration": 60.0},
    "nym5":     {"sigma": 0.5,   "duration": 120.0},
    "nym2":     {"sigma": 0.35,  "duration": 90.0},
}


def get_client_ip(visit_id: str) -> str:
    client_id = visit_id.split("_v")[0]
    return CLIENTS[client_id]["private_ip"]


def analyze_mode(mode: str) -> dict:
    jsonl = PILOT_DIR / f"{mode}_visits.jsonl"
    records = []
    with open(jsonl) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("visit_status") == "success":
                records.append(rec)

    visit_durations = []
    pkt_counts_ingress = []
    flow_spans = []  # actual packet span (last_ts - first_ts) in carved window

    skipped = 0
    for rec in records:
        visit_id = rec["visit_id"]
        t_start  = rec["t_visit_start"]
        t_end    = rec["t_visit_end"]
        visit_durations.append(t_end - t_start)

        ingress_pcap = PILOT_DIR / mode / f"{visit_id}_ingress.pcap"
        if not ingress_pcap.exists():
            skipped += 1
            continue

        try:
            client_ip = get_client_ip(visit_id)
            pkts = extract_packets(str(ingress_pcap), local_ip=client_ip)
            carved = carve_time_window(pkts, t_start - 0.5, t_end + 3.0)
        except Exception as e:
            skipped += 1
            continue

        if len(carved) < 2:
            skipped += 1
            continue

        pkt_counts_ingress.append(len(carved))
        ts_vals = [p["ts"] for p in carved]
        flow_spans.append(max(ts_vals) - min(ts_vals))

    return {
        "n_visits":       len(records),
        "n_parsed":       len(pkt_counts_ingress),
        "skipped":        skipped,
        "visit_dur_mean": np.mean(visit_durations) if visit_durations else 0,
        "visit_dur_p95":  np.percentile(visit_durations, 95) if visit_durations else 0,
        "visit_dur_max":  np.max(visit_durations) if visit_durations else 0,
        "pkt_mean":       np.mean(pkt_counts_ingress) if pkt_counts_ingress else 0,
        "pkt_p95":        np.percentile(pkt_counts_ingress, 95) if pkt_counts_ingress else 0,
        "span_mean":      np.mean(flow_spans) if flow_spans else 0,
        "span_p95":       np.percentile(flow_spans, 95) if flow_spans else 0,
        "span_max":       np.max(flow_spans) if flow_spans else 0,
    }


def suggest_params(stats: dict, mode: str) -> dict:
    """
    Heuristics:
      duration = ceil(p95 flow span + 10s buffer), rounded to nearest 30s
                 but at least 20s above mean.
      sigma    = estimated inter-packet gap at p95 packet count:
                   sigma ≈ span_mean / pkt_mean * 2  (two packet spacings)
                 clamped to [0.1, 1.0] and rounded to 2dp.
    """
    span_p95   = stats["span_p95"]
    span_mean  = stats["span_mean"]
    pkt_mean   = stats["pkt_mean"]

    # Duration: cover p95 span + buffer, snap to nearest 30s
    raw_dur = span_p95 + 10.0
    duration = max(30.0, round(raw_dur / 30.0) * 30.0)

    # Sigma: mean inter-packet gap × 2 (two-packet smoothing width)
    if pkt_mean > 1 and span_mean > 0:
        gap = span_mean / pkt_mean
        sigma = round(min(max(gap * 2, 0.1), 1.0), 2)
    else:
        sigma = CURRENT_PARAMS[mode]["sigma"]

    return {"sigma": sigma, "duration": duration}


def main():
    print("\n" + "=" * 72)
    print("  Pilot Flow Statistics — per mode")
    print("=" * 72)

    results = {}
    for mode in MODES:
        print(f"\n  [{mode}] analyzing...", end=" ", flush=True)
        try:
            stats = analyze_mode(mode)
            results[mode] = stats
            print(f"done ({stats['n_parsed']}/{stats['n_visits']} visits parsed)")
        except Exception as e:
            print(f"ERROR: {e}")
            results[mode] = None

    print("\n" + "=" * 72)
    print(f"  {'Mode':<10}  {'VisitDur(s)':>11}  {'VisitP95':>8}  {'Pkts/flow':>9}  {'Span(s)':>7}  {'SpanP95':>7}  {'SpanMax':>7}")
    print(f"  {'-'*10}  {'-'*11}  {'-'*8}  {'-'*9}  {'-'*7}  {'-'*7}  {'-'*7}")

    for mode in MODES:
        s = results[mode]
        if s is None:
            print(f"  {mode:<10}  (failed)")
            continue
        print(f"  {mode:<10}  {s['visit_dur_mean']:>11.1f}  {s['visit_dur_p95']:>8.1f}  {s['pkt_mean']:>9.1f}  {s['span_mean']:>7.1f}  {s['span_p95']:>7.1f}  {s['span_max']:>7.1f}")

    print("\n" + "=" * 72)
    print(f"  {'Mode':<10}  {'CurSigma':>8}  {'CurDur':>6}  {'SugSigma':>8}  {'SugDur':>6}  Action")
    print(f"  {'-'*10}  {'-'*8}  {'-'*6}  {'-'*8}  {'-'*6}  {'-'*20}")

    for mode in MODES:
        s = results[mode]
        if s is None:
            continue
        cur = CURRENT_PARAMS[mode]
        sug = suggest_params(s, mode)
        sigma_flag = "✓ ok" if abs(sug["sigma"] - cur["sigma"]) < 0.05 else f"→ {sug['sigma']}"
        dur_flag   = "✓ ok" if abs(sug["duration"] - cur["duration"]) < 15 else f"→ {sug['duration']}s"
        print(f"  {mode:<10}  {cur['sigma']:>8.3f}  {cur['duration']:>5.0f}s  {sug['sigma']:>8.3f}  {sug['duration']:>5.0f}s  sigma {sigma_flag}  dur {dur_flag}")

    # Focused nym2 vs nym5 comparison
    print("\n" + "=" * 72)
    print("  nym5 vs nym2 calibration detail")
    print("=" * 72)
    for mode in ("nym5", "nym2"):
        s = results.get(mode)
        if s is None:
            continue
        sug = suggest_params(s, mode)
        cur = CURRENT_PARAMS[mode]
        print(f"\n  {mode}:")
        print(f"    visits parsed          : {s['n_parsed']} / {s['n_visits']}")
        print(f"    visit duration mean/p95: {s['visit_dur_mean']:.1f}s / {s['visit_dur_p95']:.1f}s")
        print(f"    packets/flow mean/p95  : {s['pkt_mean']:.0f} / {s['pkt_p95']:.0f}")
        print(f"    flow span  mean/p95/max: {s['span_mean']:.1f}s / {s['span_p95']:.1f}s / {s['span_max']:.1f}s")
        print(f"    current params         : sigma={cur['sigma']}  duration={cur['duration']}s")
        print(f"    suggested params       : sigma={sug['sigma']}  duration={sug['duration']}s")

    print()


if __name__ == "__main__":
    main()
