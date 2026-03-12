#!/bin/bash
# scripts/github_setup.sh
# ========================
# Run ONCE to initialise the local git repo and push to GitHub.
#
# Prerequisites:
#   1. Create an EMPTY repo on GitHub named "wire-adversary-correlator"
#      (no README, no .gitignore, no license — this script handles all of that)
#   2. Have git installed and SSH key added to GitHub
#
# Usage:
#   bash scripts/github_setup.sh https://github.com/YOUR_USER/wire-adversary-correlator.git

set -euo pipefail
REMOTE_URL=${1:?"Usage: $0 <github-remote-url>"}

cd "$(dirname "$0")/.."

git init
git add .
git commit -m "chore: initial project structure

- collector/: SSH orchestrator, visit trigger, label logger
- preprocessing/: pcap_parser, KDE, windower, quartet_builder, dataset_builder
- model/: dual-CNN correlator, dataset, train, evaluate
- analysis/: shape visualizer, ablation, system comparison
- tests/: unit tests for KDE, windower, dataset
- config/: infrastructure and hyperparameter configs
- pyproject.toml: installable package for clean imports"

git branch -M main
git remote add origin "$REMOTE_URL"
git push -u origin main

echo ""
echo "✓ Repository pushed to $REMOTE_URL"
echo ""
echo "Next steps:"
echo "  1. Fill in config/infrastructure.py with your Hetzner IPs"
echo "  2. Install the package locally:  pip install -e '.[dev]'"
echo "  3. Run tests (will show NotImplementedError until stubs are filled):"
echo "       pytest tests/ -v"
echo "  4. Invite your mentors: GitHub → Settings → Collaborators"
