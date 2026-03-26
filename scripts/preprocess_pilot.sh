#!/usr/bin/env bash
# scripts/preprocess_pilot.sh
# ===========================
# Runs dataset_builder.py on each mode's pilot pcaps to produce .npz datasets.
# Run this after collect_pilot.sh completes.
#
# Usage (from repo root):
#   bash scripts/preprocess_pilot.sh
#
# Output:
#   data/pilot/baseline_dataset.npz
#   data/pilot/tor_dataset.npz
#   data/pilot/vpn_dataset.npz

set -euo pipefail

OUTPUT="data/pilot"
FAILED=()

for mode in baseline tor vpn; do
    labels="$OUTPUT/${mode}_visits.jsonl"
    data_dir="$OUTPUT/$mode"
    out_npz="$OUTPUT/${mode}_dataset.npz"

    echo ""
    echo "[$(date +%H:%M:%S)] Preprocessing $mode..."
    echo "  labels:   $labels"
    echo "  data_dir: $data_dir"
    echo "  output:   $out_npz"

    if [[ ! -f "$labels" ]]; then
        echo "  [skip] $labels not found — was collection run for $mode?"
        FAILED+=("$mode")
        continue
    fi

    if python3 -m preprocessing.dataset_builder \
        --labels   "$labels" \
        --data_dir "$data_dir" \
        --output   "$out_npz" \
        --mode     "$mode"; then
        echo "  [ok] $out_npz written"
    else
        echo "  [FAILED] dataset_builder exited with error for $mode"
        FAILED+=("$mode")
    fi
done

echo ""
echo "========================================"
echo " Preprocessing complete."
echo " .npz files:"
ls -lh "$OUTPUT"/*.npz 2>/dev/null || echo "  (none)"
if [[ ${#FAILED[@]} -gt 0 ]]; then
    echo " FAILED modes: ${FAILED[*]}"
    exit 1
fi
echo "========================================"
