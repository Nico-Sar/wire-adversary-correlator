#!/bin/bash
# scripts/collect_baseline.sh
set -euo pipefail
python collector/coordinator.py --mode baseline "$@"
