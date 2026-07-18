#!/usr/bin/env python3
"""
scripts/check_mode_ready.py
=============================
READ-ONLY. Reports whether a mode has at least one audit-passed round in
each of train/val/test, using the campaign's OWN split-per-stage manifest
(data/campaign/_url_slices/stage_manifest.txt, written by _stage_slices.py)
rather than a hardcoded round-number cutoff — vpn/tor use the 11-stage
"full"/"tor" grid, nym5/nym2 use the 7-stage "light" grid, and the two
grids' train/val/test stage boundaries differ (see docs/CAMPAIGN_RUNBOOK.md
"Two grids, one round counter"). Hardcoding e.g. "round 8 = val" is correct
for vpn/tor but wrong for nym5/nym2, which is exactly the mistake this
script exists to avoid.

Does not touch any live process or write anything under data/campaign/ —
only reads stage_manifest.txt and each round's .audit_passed marker.

Usage:
    python3 scripts/check_mode_ready.py [--campaign-root DIR] [--mode MODE]
    python3 scripts/check_mode_ready.py --mode vpn --list-rounds
        (prints one audit-passed round number per line for that mode,
        machine-readable, consumed by merge_and_stage_mode.sh)
"""
import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Which stage_manifest.txt "label" column a mode's rounds are governed by.
# vpn reads the full grid directly; tor has its own zip-filtered label but
# shares the full grid's stage index; nym5/nym2 share the light grid.
MODE_GRID_LABEL = {
    "vpn": "full",
    "tor": "tor",
    "nym5": "light",
    "nym2": "light",
}
SPLITS = ("train", "val", "test")

# Split-instance architecture: rounds 01-03 were collected before vpn/tor/
# nym2/nym5 split into separate campaign roots and live under data/campaign;
# round 04 onward restarts numbering independently in each split root, with
# vpn/tor/nym2 continuing under data/campaign_fast and nym5 under
# data/campaign_nym5 (nym2 travels with the fast root, not with nym5 --
# see run_campaign.sh's MODE_SCOPE handling). A round number alone is
# therefore ambiguous without knowing which root it physically lives in;
# audit_passed_rounds() below must check both the pre-split root and the
# mode's own post-split root, or every round >= 4 is invisible (confirmed
# live 2026-07-18: this is the same class of bug fixed in audit_stage.sh's
# budget tracker, which had the identical single-root blind spot).
PRE_SPLIT_LAST_ROUND = 3
MODE_POST_SPLIT_DIR = {
    "vpn": "campaign_fast",
    "tor": "campaign_fast",
    "nym2": "campaign_fast",
    "nym5": "campaign_nym5",
}


def parse_stage_manifest(path: Path) -> dict[str, dict[int, str]]:
    """Returns {label: {stage_num: split}} from stage_manifest.txt lines
    'label\\tstage_NN\\tsplit\\tcount'. Tor's split column is literally the
    string '(zip-filtered)' (see _stage_slices.py::write_tor_grid) — tor's
    real split is looked up via the 'full' label at the same stage number,
    since tor mirrors full's stage index by construction.
    """
    by_label: dict[str, dict[int, str]] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        label, stage_tok, split, _count = parts
        if not stage_tok.startswith("stage_"):
            continue
        # tor rows spell the token "stage_NN.txt" (see write_tor_grid);
        # full/light spell it bare "stage_NN" -- strip both forms.
        stage_num = int(stage_tok.removeprefix("stage_").removesuffix(".txt"))
        by_label.setdefault(label, {})[stage_num] = split
    return by_label


def mode_round_splits(mode: str, by_label: dict[str, dict[int, str]]) -> dict[int, str]:
    grid_label = MODE_GRID_LABEL[mode]
    if mode == "tor":
        # tor's own manifest rows say "(zip-filtered)", not a split — the
        # real split per stage is the full grid's split at the same index.
        full_stages = by_label.get("full", {})
        tor_stages = by_label.get("tor", {})
        return {n: full_stages[n] for n in tor_stages if n in full_stages}
    return dict(by_label.get(grid_label, {}))


def roots_for_mode(campaign_root: Path, mode: str) -> list[Path]:
    """Pre-split root (campaign_root itself, holding rounds 01-03) plus the
    mode's post-split root (a sibling of campaign_root), deduped in case
    campaign_root already points directly at the post-split root."""
    roots = [campaign_root]
    post_split_name = MODE_POST_SPLIT_DIR.get(mode)
    if post_split_name:
        post_root = campaign_root.parent / post_split_name
        if post_root.resolve() != campaign_root.resolve():
            roots.append(post_root)
    return roots


def audit_passed_rounds(campaign_root: Path, mode: str) -> set[int]:
    passed = set()
    for root in roots_for_mode(campaign_root, mode):
        if not root.is_dir():
            continue
        for d in root.glob("round_*"):
            if not d.is_dir():
                continue
            if (d / ".audit_passed").exists():
                try:
                    passed.add(int(d.name.removeprefix("round_")))
                except ValueError:
                    continue
    return passed


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--campaign-root", default=str(REPO_ROOT / "data" / "campaign"))
    ap.add_argument("--mode", choices=sorted(MODE_GRID_LABEL), default=None,
                     help="Check only this mode (default: all four)")
    ap.add_argument("--list-rounds", action="store_true",
                     help="With --mode: print audit-passed round numbers for "
                          "that mode, one per line, machine-readable (no "
                          "READY/NOT READY prose). Exit 0 if any rounds "
                          "found, 1 if none.")
    args = ap.parse_args()

    campaign_root = Path(args.campaign_root)
    manifest_path = campaign_root / "_url_slices" / "stage_manifest.txt"
    if not manifest_path.exists():
        print(f"[error] stage manifest not found: {manifest_path} "
              f"(has scripts/_stage_slices.py been run for this campaign yet?)",
              file=sys.stderr)
        sys.exit(2)

    by_label = parse_stage_manifest(manifest_path)
    modes = [args.mode] if args.mode else sorted(MODE_GRID_LABEL)

    if args.list_rounds:
        if not args.mode:
            print("[error] --list-rounds requires --mode", file=sys.stderr)
            sys.exit(2)
        passed_rounds = audit_passed_rounds(campaign_root, args.mode)
        round_splits = mode_round_splits(args.mode, by_label)
        ready_rounds = sorted(n for n in round_splits if n in passed_rounds)
        for n in ready_rounds:
            print(n)
        sys.exit(0 if ready_rounds else 1)

    any_not_ready = False
    for mode in modes:
        passed_rounds = audit_passed_rounds(campaign_root, mode)
        round_splits = mode_round_splits(mode, by_label)
        have_split_rounds = {s: [] for s in SPLITS}
        for round_num, split in sorted(round_splits.items()):
            if round_num in passed_rounds:
                have_split_rounds[split].append(round_num)

        missing = [s for s in SPLITS if not have_split_rounds[s]]
        ready = not missing
        any_not_ready |= not ready

        status = "READY" if ready else "NOT READY"
        detail = ", ".join(
            f"{s}={have_split_rounds[s] or 'none'}" for s in SPLITS
        )
        print(f"{mode:6s} [{status:9s}] {detail}")
        if missing:
            print(f"         missing audit-passed round(s) for: {', '.join(missing)}")

    sys.exit(1 if any_not_ready else 0)


if __name__ == "__main__":
    main()
