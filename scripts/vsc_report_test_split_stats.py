"""
scripts/vsc_report_test_split_stats.py
=======================================
Reports, per mode, the held-out test-split URL/flow/pair counts and natural
base rate -- the numbers evaluate.py prints once per run (Section~sec:setup
of the thesis quotes this for VPN already; this fills in Tor, Nym 5-hop,
Nym 2-hop). Recomputes directly from each mode's production dataset via the
identical split logic evaluate.py uses, rather than grepping old run logs,
so it's correct regardless of which job/seed produced the current results.

Run ON VSC, from a compute node:
  srun -M mindwell -A lp_pets -p batch_graniterapids --time=00:05:00 --mem=8G \\
      bash -l -c 'module load Python/3.12.3-GCCcore-13.3.0; \\
      python3 scripts/vsc_report_test_split_stats.py'
"""
import numpy as np
from model.evaluate import _get_split_indices

DATA_ROOT = "/data/leuven/388/vsc38858/wire-adversary-correlator/wire-adversary-correlator-data/datasets"

# (mode, dataset filename) -- must match whichever dataset each mode's
# CURRENT production results/{mode}_seed*/ was actually trained/evaluated on.
DATASETS = [
    ("vpn",  f"{DATA_ROOT}/vpn/vpn_wl10_merged.npz"),
    ("tor",  f"{DATA_ROOT}/tor/tor_wl30_merged.npz"),
    ("nym2", f"{DATA_ROOT}/nym2/nym2_wl30_merged.npz"),
    ("nym5", f"{DATA_ROOT}/nym5/nym5_merged.npz"),
]

for mode, path in DATASETS:
    data = np.load(path, allow_pickle=True)
    split_pos, _, _, test_urls, _ = _get_split_indices(data, "test")
    ing_indices = sorted({i for i, _ in split_pos})
    eg_indices = sorted({e for _, e in split_pos})
    n_cross = len(ing_indices) * len(eg_indices)
    n_pos = len(split_pos)
    base_rate = n_pos / n_cross if n_cross else float("nan")
    print(f"{mode}: {len(test_urls)} URLs, {len(ing_indices)} ingress flows, "
          f"{len(eg_indices)} egress flows -> {n_cross:,} cross-product pairs, "
          f"{n_pos} positives, base_rate={base_rate:.2e}")
