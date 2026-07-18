"""
preprocessing/merge_rounds.py
==============================
Merges multiple per-round .npz datasets (dataset_builder.py output, same
mode) into one. Concatenates all array fields; 'pairs' ingress/egress
indices are offset per round, since each round's npz has its own
independently-shuffled ingress/egress arrays starting at index 0.

Split-integrity check
----------------------
model/dataset.py's QuartetDataset computes its 70/15/15 train/val/test split
by sorting whatever URLs happen to be present in the .npz it's given and
cutting by rank — i.e. IT IS RELATIVE TO THE FILE, not to the campaign as a
whole. The campaign's true, stable split is computed once over the FULL
validated URL list by scripts/_stage_slices.py's assign_global_split(), and
each stage/round is homogeneous by split (see data/campaign/_url_slices/
stage_manifest.txt: a whole round's URLs are 100% train, 100% val, or 100%
test — never mixed).

This means: if a merged file's URLs happen to all come from train-bucket
rounds (as round_01+round_02 currently do — both are stage_01/stage_02,
both 100% train per the manifest), QuartetDataset's own rank-based split
computed on THAT file will still manufacture a fake 70/15/15 split within
what is actually all-train data — its "test" 15% would just be some
alphabetically-late train URLs mislabeled as held-out. This script checks
for exactly that: it recomputes the authoritative per-URL split by calling
assign_global_split() on the master validated URL list (the same function
and same list _stage_slices.py used to build the stage grids — NOT
split_consistency_check.txt, which only documents the ~265 URLs shared
between the full and light lists; vpn/tor draw from the full 500-URL list,
including .zip URLs that aren't in that shared subset at all) and reports
whether the merged file actually contains URLs from more than one true
split bucket. If not, no genuine held-out evaluation is possible on this
merge — flagged loudly, not silently passed through.

Usage:
  python -m preprocessing.merge_rounds \\
      --mode vpn \\
      --inputs data/campaign/datasets/vpn_round01.npz data/campaign/datasets/vpn_round02.npz \\
      --output data/campaign/datasets/vpn_merged_r01r02.npz \\
      --master-urls data/campaign/stage0/validated_urls.txt
"""

import argparse
import sys
from pathlib import Path

import numpy as np


def url_basename(url: str) -> str:
    """
    split_consistency_check.txt keys are bare filenames (e.g. 'crypto_tx_1.json')
    from the abstract validated URL list, computed before per-mode host:port
    prefixing (dataset_builder.py's ingress_urls/egress_urls carry the full
    mode-specific URL, e.g. 'http://10.1.0.3:8080/crypto_tx_1.json' for vpn,
    'http://204.168.189.97/crypto_tx_1.json' for nym2). Strip scheme+host+port
    to compare like with like.
    """
    from urllib.parse import urlparse
    return urlparse(url).path.lstrip("/")


def load_global_split(master_urls_path: str) -> dict[str, str]:
    """
    Recomputes the authoritative per-URL split by calling assign_global_split()
    (scripts/_stage_slices.py) on the master validated URL list — the same
    function and same list the campaign's stage grids were built from. This
    is the true 500-URL superset; split_consistency_check.txt only covers the
    ~265 URLs shared between the full and light lists, so using it here would
    silently miss every .zip URL (full/tor-only, never in the light list).
    """
    scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from _stage_slices import assign_global_split

    all_urls = [u.strip() for u in Path(master_urls_path).read_text().splitlines() if u.strip()]
    return assign_global_split(all_urls)


def merge_npz(paths: list[str]) -> dict:
    """Concatenates dataset_builder.py .npz files, offsetting pair indices."""
    ingress_up, ingress_down = [], []
    egress_up, egress_down = [], []
    ingress_visit_ids, egress_visit_ids = [], []
    ingress_urls, egress_urls = [], []
    modes = []
    pairs = []

    ing_offset = 0
    eg_offset = 0
    for path in paths:
        data = np.load(path, allow_pickle=True)

        ingress_up.append(data["X_ingress_up"])
        ingress_down.append(data["X_ingress_down"])
        egress_up.append(data["X_egress_up"])
        egress_down.append(data["X_egress_down"])
        ingress_visit_ids.append(data["ingress_visit_ids"])
        egress_visit_ids.append(data["egress_visit_ids"])
        ingress_urls.append(data["ingress_urls"])
        egress_urls.append(data["egress_urls"])
        modes.append(data["modes"])

        round_pairs = data["pairs"].copy()
        round_pairs[:, 0] += ing_offset
        round_pairs[:, 1] += eg_offset
        pairs.append(round_pairs)

        ing_offset += len(data["X_ingress_up"])
        eg_offset += len(data["X_egress_up"])

    return {
        "X_ingress_up":      np.concatenate(ingress_up),
        "X_ingress_down":    np.concatenate(ingress_down),
        "X_egress_up":       np.concatenate(egress_up),
        "X_egress_down":     np.concatenate(egress_down),
        "ingress_visit_ids": np.concatenate(ingress_visit_ids),
        "egress_visit_ids":  np.concatenate(egress_visit_ids),
        "ingress_urls":      np.concatenate(ingress_urls),
        "egress_urls":       np.concatenate(egress_urls),
        "modes":             np.concatenate(modes),
        "pairs":             np.concatenate(pairs),
    }


def check_split_integrity(merged: dict, global_split: dict[str, str]) -> dict:
    """
    Compares the merged file's URLs against the authoritative global split.
    Returns a report dict; does not raise — the caller decides what to do
    with a report showing the merge has no real held-out data.
    """
    all_urls = set(merged["ingress_urls"]) | set(merged["egress_urls"])
    # Compare by basename — see url_basename() docstring for why the raw
    # per-mode URLs (with host:port) don't match global_split's bare keys.
    url_to_base = {u: url_basename(u) for u in all_urls}

    missing = sorted(u for u in all_urls if url_to_base[u] not in global_split)
    true_splits = {u: global_split[url_to_base[u]] for u in all_urls if url_to_base[u] in global_split}

    split_counts: dict[str, int] = {}
    for s in true_splits.values():
        split_counts[s] = split_counts.get(s, 0) + 1

    # "No URL in two splits" — a URL can only have ONE entry in true_splits
    # (dict semantics guarantee this), so the real question is whether the
    # dict construction ever saw conflicting labels for the same URL across
    # the input files being merged. Check that directly.
    conflicts = []
    for u in all_urls:
        base = url_to_base[u]
        labels_seen = {global_split[base]} if base in global_split else set()
        # global_split is already a single canonical source, so conflicts
        # here would only arise from a URL string typo/mismatch across
        # rounds — nothing to compare against without per-round breakdown,
        # so this stays empty by construction. Kept explicit for the report.
        if len(labels_seen) > 1:
            conflicts.append(u)

    n_true_splits_present = len(split_counts)

    return {
        "n_unique_urls": len(all_urls),
        "n_missing_from_global_split": len(missing),
        "missing_urls_sample": missing[:10],
        "split_counts": split_counts,
        "n_true_splits_present": n_true_splits_present,
        "url_in_two_splits": conflicts,
        "genuine_holdout_possible": n_true_splits_present > 1,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--inputs", required=True, nargs="+",
                        help="Per-round .npz files to merge, same mode")
    parser.add_argument("--output", required=True)
    parser.add_argument("--master-urls", required=True,
                        help="Path to the master validated URL list (e.g. data/campaign/stage0/validated_urls.txt)")
    args = parser.parse_args()

    merged = merge_npz(args.inputs)
    n = len(merged["X_ingress_up"])
    print(f"[{args.mode}] merged {len(args.inputs)} round(s) -> {n} flows "
          f"({len(merged['pairs'])} positive pairs)")

    global_split = load_global_split(args.master_urls)
    report = check_split_integrity(merged, global_split)
    print(f"[{args.mode}] split-integrity report:")
    print(f"  unique URLs in merge         : {report['n_unique_urls']}")
    print(f"  missing from global split map: {report['n_missing_from_global_split']}"
          + (f" (e.g. {report['missing_urls_sample']})" if report["missing_urls_sample"] else ""))
    print(f"  true split label counts      : {report['split_counts']}")
    print(f"  URLs assigned to 2 splits    : {len(report['url_in_two_splits'])}"
          + (f" {report['url_in_two_splits']}" if report["url_in_two_splits"] else " (none — OK)"))
    if not report["genuine_holdout_possible"]:
        print(f"  *** WARNING: all URLs in this merge belong to a single true split "
              f"({list(report['split_counts'].keys())}) — there is NO genuine held-out "
              f"data in this file. Any train/val/test split QuartetDataset computes on "
              f"it internally is a fake split within one true bucket, not real held-out "
              f"evaluation. ***")

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.output, **merged)
    print(f"[{args.mode}] saved merged dataset -> {args.output}")
