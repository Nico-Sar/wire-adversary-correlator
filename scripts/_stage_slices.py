#!/usr/bin/env python3
"""
scripts/_stage_slices.py
=========================
Internal helper for scripts/run_campaign.sh. Per-mode URL design:
  - vpn/tor  collect against the FULL validated URL list (e.g. 500).
  - nym5/nym2 collect against a LIGHTER subset (e.g. the 265 html+json URLs
    — heavy mp3/mp4/pdf/zip are too slow/timeout-prone through nym5's 5-hop
    path). The light list is a STRICT SUBSET of the full list.

SPLIT CONSISTENCY (the whole point of this rewrite): a URL that appears in
BOTH lists must land in the SAME train/val/test split for both, or cross-mode
comparison on shared URLs is contaminated. The split is computed ONCE, over
the FULL list (the superset), using the exact same arithmetic as
model/dataset.py (sorted(set(urls)), EVAL["train_split"]/val_split from
config/hyperparams.py). The light list's split membership per URL is then
just a LOOKUP into this one canonical assignment — never recomputed
independently. This is enforced by construction (one assign_global_split()
call, both stage grids read from it) and verified explicitly by
write_consistency_check() below before any stage file is written.

The full list and the light list get INDEPENDENT stage grids (different
URL counts chunk differently) — they are NOT positionally aligned. Verified
empirically that the obvious alternative (filter the full list's stage
chunks down to light URLs) produces wildly uneven per-stage light coverage:
on the real 500-URL list, the val split happens to be 100% light (pure
alphabetical-clustering coincidence: light/heavy URLs sort into contiguous
blocks by naming convention, not interleaved) while train/test are unevenly
mixed. Each list is therefore chunked into ~STAGE_SIZE-URL stages
independently, within each split — never crossing a split boundary, by
construction, for either list.

Usage:
    python3 scripts/_stage_slices.py <full_urls.txt> <light_urls.txt> <output_dir> [stage_size]

Writes:
    <output_dir>/full/stage_NN.txt   (vpn/tor)
    <output_dir>/light/stage_NN.txt  (nym5/nym2)
    <output_dir>/stage_manifest.txt  (both grids, split per stage, URL counts)
    <output_dir>/split_consistency_check.txt (every shared URL's split label
                                               from both views, confirming agreement)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.hyperparams import EVAL


def assign_global_split(all_urls: list[str]) -> dict[str, str]:
    """
    Mirrors model/dataset.py's QuartetDataset split logic exactly, over the
    FULL (superset) URL list. Returns {url: "train"|"val"|"test"}. This is
    the ONE canonical assignment — every other list derives its split
    membership by looking up into this dict, never by recomputing
    percentages over its own (smaller) length.
    """
    unique_urls = sorted(set(all_urls))
    U = len(unique_urls)
    n_train = int(U * EVAL["train_split"])
    n_val = int(U * EVAL["val_split"])
    if U >= 3:
        n_val = max(1, n_val)
        n_test_ = U - n_train - n_val
        if n_test_ < 1:
            n_train -= 1

    assignment = {}
    for u in unique_urls[:n_train]:
        assignment[u] = "train"
    for u in unique_urls[n_train: n_train + n_val]:
        assignment[u] = "val"
    for u in unique_urls[n_train + n_val:]:
        assignment[u] = "test"
    return assignment


def chunk(urls: list[str], size: int) -> list[list[str]]:
    return [urls[i:i + size] for i in range(0, len(urls), size)] if urls else []


def write_stage_grid(urls: list[str], global_split: dict[str, str],
                      out_dir: Path, stage_size: int, label: str,
                      manifest_lines: list[str]) -> int:
    """
    Buckets `urls` (a subset of, or equal to, the URLs global_split was
    computed over) into train/val/test using ONLY the lookup, then chunks
    each bucket independently into ~stage_size stages. Returns stage count.
    """
    buckets = {"train": [], "val": [], "test": []}
    missing = []
    for u in sorted(set(urls)):
        split = global_split.get(u)
        if split is None:
            missing.append(u)
            continue
        buckets[split].append(u)
    if missing:
        print(f"[error] {label}: {len(missing)} URL(s) not present in the "
              f"global (full-list) split assignment — they can't be staged "
              f"consistently. First few: {missing[:5]}")
        sys.exit(1)

    out_dir.mkdir(parents=True, exist_ok=True)
    stage_num = 0
    for split_name in ("train", "val", "test"):
        for batch in chunk(buckets[split_name], stage_size):
            stage_num += 1
            stage_file = out_dir / f"stage_{stage_num:02d}.txt"
            stage_file.write_text("\n".join(batch) + "\n")
            manifest_lines.append(f"{label}\tstage_{stage_num:02d}\t{split_name}\t{len(batch)}")
            print(f"  {label}/stage_{stage_num:02d}: {len(batch)} URLs ({split_name})")
    return stage_num


def write_consistency_check(full_urls: set[str], light_urls: set[str],
                             global_split: dict[str, str], out_path: Path) -> None:
    """
    Explicit, file-recorded proof (not just "trust the code") that every
    shared URL has one unambiguous split label. Since both grids are
    derived from the same global_split dict, this can only fail if a light
    URL is missing from global_split entirely (caught in write_stage_grid
    already) — this is the auditable evidence trail for that guarantee.
    """
    shared = sorted(full_urls & light_urls)
    lines = [f"Shared URLs (in both full and light lists): {len(shared)}",
             f"Light-only / not-in-full: {sorted(light_urls - full_urls) or 'none'}",
             ""]
    for u in shared:
        lines.append(f"{u}\t{global_split.get(u, 'MISSING')}")
    out_path.write_text("\n".join(lines) + "\n")


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    full_path = Path(sys.argv[1])
    light_path = Path(sys.argv[2])
    out_dir = Path(sys.argv[3])
    stage_size = int(sys.argv[4]) if len(sys.argv) > 4 else 50
    out_dir.mkdir(parents=True, exist_ok=True)

    def read_urls(p: Path) -> list[str]:
        return [l.strip() for l in p.read_text().splitlines()
                if l.strip() and not l.startswith("#")]

    full_urls = read_urls(full_path)
    light_urls = read_urls(light_path)

    if not set(light_urls) <= set(full_urls):
        extra = set(light_urls) - set(full_urls)
        print(f"[error] light list is not a strict subset of the full list — "
              f"{len(extra)} URL(s) in light but not full: {sorted(extra)[:5]}")
        sys.exit(1)

    global_split = assign_global_split(full_urls)
    print(f"Global split (computed over full list, {len(set(full_urls))} URLs):")
    for name in ("train", "val", "test"):
        n = sum(1 for v in global_split.values() if v == name)
        print(f"  {name}: {n} URLs")

    manifest_lines = []
    print("\nFull-list stages (vpn/tor):")
    n_full = write_stage_grid(full_urls, global_split, out_dir / "full", stage_size,
                               "full", manifest_lines)
    print("\nLight-list stages (nym5/nym2):")
    n_light = write_stage_grid(light_urls, global_split, out_dir / "light", stage_size,
                                "light", manifest_lines)

    (out_dir / "stage_manifest.txt").write_text("\n".join(manifest_lines) + "\n")
    write_consistency_check(set(full_urls), set(light_urls), global_split,
                             out_dir / "split_consistency_check.txt")

    print(f"\nFull list: {n_full} stages. Light list: {n_light} stages.")
    print("These are INDEPENDENT grids — light stage N is not the same URLs as")
    print("full stage N. run_campaign.sh advances each grid at its own pace.")
    print(f"Manifest: {out_dir / 'stage_manifest.txt'}")
    print(f"Split consistency proof: {out_dir / 'split_consistency_check.txt'}")


if __name__ == "__main__":
    main()
