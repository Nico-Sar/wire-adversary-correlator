#!/bin/bash
# scripts/collect_vpn.sh
set -euo pipefail
python collector/coordinator.py --mode vpn "$@"
