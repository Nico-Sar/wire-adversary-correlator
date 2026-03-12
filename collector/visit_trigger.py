"""
collector/visit_trigger.py
==========================
Runs on the CLIENT VM. Called by coordinator.py via SSH.
Launches a headless Firefox browser visit and prints metadata as JSON to stdout.

Deploy to ~/collector/visit_trigger.py on each client VM.

Usage (called by coordinator, not directly):
  python3 visit_trigger.py --url example.com --visit_id abc123 [--proxy socks5://...]
"""

import argparse
import json
import sys
import time

from playwright.sync_api import sync_playwright


def visit(url: str, visit_id: str, proxy: str | None) -> dict:
    """
    Launches a headless Firefox browser, navigates to the URL,
    waits for page load + settle time, then closes.
    Returns a metadata dict serialized to stdout as JSON.
    """
    raise NotImplementedError


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url",      required=True)
    parser.add_argument("--visit_id", required=True)
    parser.add_argument("--proxy",    default=None)
    args = parser.parse_args()

    result = visit(args.url, args.visit_id, args.proxy)
    print(json.dumps(result))
    sys.exit(0 if result.get("status") == "success" else 1)
