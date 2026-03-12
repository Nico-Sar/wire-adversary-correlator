#!/bin/bash
# scripts/preprocess_all.sh
# Runs dataset_builder.py for all four modes.
set -euo pipefail

INGRESS_DIR=${INGRESS_DIR:-"./data/raw/ingress"}
EGRESS_DIR=${EGRESS_DIR:-"./data/raw/egress"}
LABELS_DIR=${LABELS_DIR:-"./logs"}
OUTPUT_DIR=${OUTPUT_DIR:-"./data"}

for mode in nym tor vpn baseline; do
    labels="${LABELS_DIR}/labels_${mode}.jsonl"
    if [ -f "$labels" ]; then
        echo "[*] Processing $mode..."
        python preprocessing/dataset_builder.py \
            --labels      "$labels" \
            --ingress_dir "$INGRESS_DIR" \
            --egress_dir  "$EGRESS_DIR" \
            --output      "${OUTPUT_DIR}/${mode}_dataset.npz" \
            --mode        "$mode"
    else
        echo "[!] Skipping $mode — no labels file found at $labels"
    fi
done
