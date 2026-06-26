#!/usr/bin/env python3
"""
scripts/_stage_slices.py
=========================
Internal helper for scripts/run_campaign.sh. Splits a validated, alphabetically
sorted URL list into per-stage slice files, guaranteeing no stage straddles a
train/val/test boundary.

Split math is copy-aligned with model/dataset.py's split (same train_split/
val_split from config/hyperparams.py, same guard for small U, same
sorted(set(urls)) ordering assumption) — this script does NOT recompute the
split independently; it mirrors the exact arithmetic so stage boundaries fall
exactly where dataset.py will draw them at training time.

Within each split (train/val/test), URLs are chunked into ~STAGE_SIZE-URL
stages independently — a chunk never crosses from one split into another,
so by construction no stage can straddle a split boundary. The last chunk in
each split absorbs the remainder (e.g. 75 val URLs at STAGE_SIZE=50 becomes
one 50-URL stage and one 25-URL stage, both entirely within val).

Usage:
    python3 scripts/_stage_slices.py <validated_urls.txt> <output_dir> [stage_size]

Writes <output_dir>/stage_NN.txt for each stage, plus stage_manifest.txt
(stage -> split -> url count).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.hyperparams import EVAL


def compute_split(unique_urls: list[str]) -> dict[str, list[str]]:
    """Mirrors model/dataset.py's QuartetDataset split logic exactly."""
    U = len(unique_urls)
    n_train = int(U * EVAL["train_split"])
    n_val = int(U * EVAL["val_split"])
    if U >= 3:
        n_val = max(1, n_val)
        n_test_ = U - n_train - n_val
        if n_test_ < 1:
            n_train -= 1
    return {
        "train": unique_urls[:n_train],
        "val": unique_urls[n_train: n_train + n_val],
        "test": unique_urls[n_train + n_val:],
    }


def chunk(urls: list[str], size: int) -> list[list[str]]:
    return [urls[i:i + size] for i in range(0, len(urls), size)] if urls else []


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    validated_path = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    stage_size = int(sys.argv[3]) if len(sys.argv) > 3 else 50
    out_dir.mkdir(parents=True, exist_ok=True)

    urls = sorted(set(
        line.strip() for line in validated_path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ))
    if not urls:
        print(f"[error] no URLs found in {validated_path}")
        sys.exit(1)

    splits = compute_split(urls)
    print(f"Total validated URLs: {len(urls)}")
    for name, split_urls in splits.items():
        print(f"  {name}: {len(split_urls)} URLs "
              f"({split_urls[0] if split_urls else '-'} .. "
              f"{split_urls[-1] if split_urls else '-'})")

    stage_num = 0
    manifest_lines = []
    for split_name in ("train", "val", "test"):
        for batch in chunk(splits[split_name], stage_size):
            stage_num += 1
            stage_file = out_dir / f"stage_{stage_num:02d}.txt"
            stage_file.write_text("\n".join(batch) + "\n")
            manifest_lines.append(f"stage_{stage_num:02d}\t{split_name}\t{len(batch)}")
            print(f"  stage_{stage_num:02d}: {len(batch)} URLs ({split_name})")

    (out_dir / "stage_manifest.txt").write_text("\n".join(manifest_lines) + "\n")
    print(f"\n{stage_num} stages written to {out_dir}")
    print(f"Manifest: {out_dir / 'stage_manifest.txt'}")


if __name__ == "__main__":
    main()
