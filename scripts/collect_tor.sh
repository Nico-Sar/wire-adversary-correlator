#!/bin/bash
# scripts/collect_tor.sh
set -euo pipefail
python collector/coordinator.py --mode tor "$@"
