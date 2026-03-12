#!/bin/bash
# scripts/collect_nym.sh
set -euo pipefail
python collector/coordinator.py --mode nym "$@"
