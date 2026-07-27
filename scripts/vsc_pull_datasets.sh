#!/usr/bin/env bash
# DEPRECATED 2026-07-27: points at mode_datasets/{mode}_merged.npz in the
# data repo, which no longer exists -- current layout is
# datasets/{mode}/{mode}_merged.npz (see wire-adversary-correlator-data's
# own history). Use a sparse clone instead (see
# figures/phase1_shapes/README.md for the exact recipe) -- confirmed
# working live on VSC 2026-07-27. Kept for reference only, do not run.
#
# scripts/vsc_pull_datasets.sh
# ===============================
# Pulls merged per-mode .npz datasets from the GitHub data repo's
# mode_datasets/ path directly via `gh api` (raw content), one file per
# mode -- NOT a git clone. The data repo also holds the full pcap backup
# (tens of GB), so a normal clone/pull would be enormous; `gh api` with
# the raw media type fetches just the requested blob (works for private
# repos, files up to 100MB -- our merged datasets are a few MB to a few
# tens of MB, well within that). Requires `gh` authenticated, which VSC
# already has (git credential.helper is `gh auth git-credential`,
# confirmed working 2026-07-18).
#
# Run ON VSC:
#   bash scripts/vsc_pull_datasets.sh vpn tor nym2
#   bash scripts/vsc_pull_datasets.sh              # all 4 modes, skips any not found

set -uo pipefail

BUNDLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATASETS_DIR="$BUNDLE_DIR/datasets"
REPO="Nico-Sar/wire-adversary-correlator-data"

log() { echo "[$(date '+%H:%M:%S')] [vsc_pull_datasets] $*"; }

command -v gh >/dev/null 2>&1 || { echo "[ERROR] gh CLI not found" >&2; exit 1; }

MODES=("$@")
[[ ${#MODES[@]} -eq 0 ]] && MODES=(vpn tor nym5 nym2)

mkdir -p "$DATASETS_DIR"
for MODE in "${MODES[@]}"; do
    OUT="$DATASETS_DIR/${MODE}_merged.npz"
    log "fetching ${MODE}_merged.npz ..."
    if gh api -H "Accept: application/vnd.github.raw+json" \
        "repos/$REPO/contents/mode_datasets/${MODE}_merged.npz" > "$OUT.tmp" 2>/tmp/vsc_pull_${MODE}.err; then
        mv "$OUT.tmp" "$OUT"
        log "$MODE: saved to $OUT ($(du -h "$OUT" | cut -f1))"
    else
        rm -f "$OUT.tmp"
        log "$MODE: FAILED (not staged yet, or gh error) -- see /tmp/vsc_pull_${MODE}.err"
    fi
done
