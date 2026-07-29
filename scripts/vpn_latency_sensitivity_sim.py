"""
scripts/vpn_latency_sensitivity_sim.py
=======================================
NOT a measurement. A robustness/sensitivity SIMULATION for the thesis
limitations section, addressing one specific question: does the window_len
sweep's conclusion for VPN (no signal across 10-40 samples, Section~sec:setup)
depend on VPN's real flows being short because of same-datacenter testbed
latency specifically, rather than on window_len itself being the wrong lever?

Method: takes VPN's real round_01 packet timestamps, applies a synthetic
stretch + jitter transform (stretch_factor=10, matching VPN's measured max
span (3.29s) to Tor's tuned analysis duration (32s); jitter_sigma=0.1s, a
representative WAN jitter magnitude), then re-runs the IDENTICAL window_len
sweep (10/15/20/30/40 samples) on this synthetic variant and compares the
resulting validation-PR-AUC pattern against the real (unstretched) VPN
sweep.

This is a crude proxy, not a network simulation: it does not model TCP
congestion control, retransmission, or slow-start behavior a genuinely
higher-RTT connection would exhibit, only a uniform timeline stretch plus
independent per-packet jitter. Results characterize sensitivity to this one
simplified latency model, not "VPN's true correlatability corrected for
latency" -- explicitly NOT used to alter, replace, or reinterpret the real
empirical VPN numbers reported elsewhere in the thesis.

Usage (run ON a host with the real round_01 pcaps + tshark, e.g. leroy):
  python -m scripts.vpn_latency_sensitivity_sim \
      --labels .../campaign/round_01/vpn_visits.jsonl \
      --data_dir .../campaign/round_01/vpn \
      --output-dir /tmp/vpn_latency_sim
"""

import argparse
import json
import logging
from pathlib import Path

import numpy as np

from config.infrastructure import EGRESS_ROUTER, get_client_private_ip
from preprocessing.kde import kde_shape, split_directions
from preprocessing.pcap_parser import extract_packets
from preprocessing.windower import carve_time_window, slice_windows

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

STRETCH_FACTOR = 10.0   # VPN max span (3.29s) * 10 ~= 32.9s ~= tor's tuned duration (32s)
JITTER_SIGMA_S = 0.1    # representative WAN jitter magnitude
NEW_DURATION_S = 32.0   # matches tor's analysis duration, for direct comparability
SIGMA_S = 0.125         # unchanged from vpn's real KDE bandwidth -- only timing is stretched
T_SAMPLE_S = 0.1
WINDOW_LENS = [10, 15, 20, 30, 40]  # identical candidates to the real vpn sweep
OVERLAP = 0.5


def stretch_and_jitter(timestamps: list[float], rng: np.random.Generator) -> list[float]:
    """t' = t * STRETCH_FACTOR + N(0, JITTER_SIGMA_S), clipped to >= 0."""
    if not timestamps:
        return []
    t = np.array(timestamps, dtype=np.float64) * STRETCH_FACTOR
    t = t + rng.normal(0.0, JITTER_SIGMA_S, size=t.shape)
    t = np.clip(t, 0.0, None)
    return t.tolist()


def compute_synthetic_quartet(ingress_pcap: str, egress_pcap: str, t_start: float, t_end: float,
                               client_private_ip: str, window_len: int, rng: np.random.Generator) -> dict:
    ingress_local_ip = client_private_ip
    egress_local_ip = EGRESS_ROUTER["private_ip"]

    ingress_pkts = extract_packets(ingress_pcap, local_ip=ingress_local_ip)
    egress_pkts = extract_packets(egress_pcap, local_ip=egress_local_ip)

    ingress_carved = carve_time_window(ingress_pkts, t_start - 0.5, t_end + 3.0)
    egress_carved = carve_time_window(egress_pkts, t_start - 0.5, t_end + 3.0)

    ingress_up_ts, ingress_down_ts = split_directions(ingress_carved)
    egress_up_ts, egress_down_ts = split_directions(egress_carved)

    # The synthetic transform: stretch + jitter applied to real timestamps,
    # nothing else in the pipeline changes.
    ingress_up_ts = stretch_and_jitter(ingress_up_ts, rng)
    ingress_down_ts = stretch_and_jitter(ingress_down_ts, rng)
    egress_up_ts = stretch_and_jitter(egress_up_ts, rng)
    egress_down_ts = stretch_and_jitter(egress_down_ts, rng)

    kde_kwargs = dict(duration=NEW_DURATION_S, sigma=SIGMA_S, t_sample=T_SAMPLE_S)
    ingress_up_shape = kde_shape(ingress_up_ts, **kde_kwargs)
    ingress_down_shape = kde_shape(ingress_down_ts, **kde_kwargs)
    egress_up_shape = kde_shape(egress_up_ts, **kde_kwargs)
    egress_down_shape = kde_shape(egress_down_ts, **kde_kwargs)

    win_kwargs = dict(window_len=window_len, overlap=OVERLAP)
    return {
        "ingress_up": slice_windows(ingress_up_shape, **win_kwargs),
        "ingress_down": slice_windows(ingress_down_shape, **win_kwargs),
        "egress_up": slice_windows(egress_up_shape, **win_kwargs),
        "egress_down": slice_windows(egress_down_shape, **win_kwargs),
        "n_ingress_up": len(ingress_up_ts), "n_ingress_down": len(ingress_down_ts),
        "n_egress_up": len(egress_up_ts), "n_egress_down": len(egress_down_ts),
    }


def build_synthetic_dataset(labels_jsonl: str, data_dir: str, window_len: int,
                             seed: int = 42, max_visits: int | None = None) -> dict:
    data_dir = Path(data_dir)
    rng = np.random.default_rng(seed)
    records = []
    with open(labels_jsonl) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("visit_status") == "success":
                records.append(rec)

    if max_visits is not None and len(records) > max_visits:
        # Single-threaded pipeline (unlike dataset_builder.py's parallel
        # workers) is far slower per visit -- this is a quick sensitivity
        # check, not a full-power measurement, so a bounded subsample is a
        # deliberate speed/precision tradeoff, not a shortcut on validity.
        idx = np.random.default_rng(seed).choice(len(records), size=max_visits, replace=False)
        records = [records[i] for i in idx]

    log.info(f"Found {len(records)} successful visits (capped to {max_visits})" if max_visits else f"Found {len(records)} successful visits")

    ingress_up, ingress_down, egress_up, egress_down = [], [], [], []
    ingress_urls, egress_urls = [], []
    n_skipped = 0
    for i, rec in enumerate(records):
        visit_id = rec["visit_id"]
        url = rec["url"]
        client_id = visit_id.split("_v")[0] if "_v" in visit_id else visit_id
        ingress_pcap = data_dir / f"{visit_id}_ingress.pcap"
        egress_pcap = data_dir / f"{visit_id}_egress.pcap"
        if not ingress_pcap.exists() or not egress_pcap.exists():
            n_skipped += 1
            continue
        try:
            client_ip = get_client_private_ip(client_id)
            q = compute_synthetic_quartet(
                str(ingress_pcap), str(egress_pcap),
                rec["t_visit_start"], rec["t_visit_end"],
                client_ip, window_len, rng,
            )
        except Exception as e:
            log.warning(f"skip {visit_id}: {e}")
            n_skipped += 1
            continue
        if min(len(q["ingress_up"]), len(q["ingress_down"]), len(q["egress_up"]), len(q["egress_down"])) == 0:
            n_skipped += 1
            continue
        ingress_up.append(q["ingress_up"]); ingress_down.append(q["ingress_down"])
        egress_up.append(q["egress_up"]); egress_down.append(q["egress_down"])
        ingress_urls.append(url); egress_urls.append(url)
        if (i + 1) % 500 == 0:
            log.info(f"  processed {i+1}/{len(records)}")

    n = len(ingress_up)
    log.info(f"Built {n} synthetic flows ({n_skipped} skipped), window_len={window_len}")
    idx = np.arange(n)
    pairs = np.stack([idx, idx, np.ones(n, dtype=np.int32)], axis=1)
    return {
        "X_ingress_up": np.stack(ingress_up), "X_ingress_down": np.stack(ingress_down),
        "X_egress_up": np.stack(egress_up), "X_egress_down": np.stack(egress_down),
        "ingress_visit_ids": np.array([r["visit_id"] for r in records[:n]]),
        "egress_visit_ids": np.array([r["visit_id"] for r in records[:n]]),
        "ingress_urls": np.array(ingress_urls), "egress_urls": np.array(egress_urls),
        "modes": np.array(["vpn"] * n), "pairs": pairs,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--data_dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-visits", type=int, default=None,
                         help="Subsample cap -- this pipeline is single-threaded (unlike "
                              "dataset_builder.py), so a bounded sample keeps the sensitivity "
                              "check practical without needing full statistical power.")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for wl in WINDOW_LENS:
        log.info(f"=== window_len={wl} (synthetic, stretch={STRETCH_FACTOR}x, jitter={JITTER_SIGMA_S}s) ===")
        data = build_synthetic_dataset(args.labels, args.data_dir, wl, max_visits=args.max_visits)
        out_path = out_dir / f"vpn_synthetic_wl{wl}.npz"
        np.savez_compressed(out_path, **data)
        log.info(f"Saved {out_path}")
