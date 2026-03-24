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
    meta = {
        "visit_id":   visit_id,
        "url":        url,
        "proxy":      proxy,
        "t_start":    time.time(),
        "t_end":      None,
        "duration_s": None,
        "status":     "started",
    }

    try:
        with sync_playwright() as p:
            launch_kwargs = {}
            if proxy:
                launch_kwargs["proxy"] = {"server": proxy}

            browser = p.firefox.launch(headless=True, **launch_kwargs)
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                ignore_https_errors=True,
            )
            page = context.new_page()

            try:
                # Navigate and wait for the load event
                page.goto(
                    f"http://{url}" if not url.startswith("http") else url,
                    wait_until="load",
                    timeout=30000,
                )

                # Settle time — lets trailing requests (ACKs, keep-alives) complete
                time.sleep(3)

                meta["status"] = "success"

            except Exception as e:
                meta["status"] = f"error: {e}"

            finally:
                browser.close()

    except Exception as e:
        meta["status"] = f"launch_error: {e}"

    meta["t_end"]      = time.time()
    meta["duration_s"] = round(meta["t_end"] - meta["t_start"], 3)
    return meta


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url",      required=True)
    parser.add_argument("--visit_id", required=True)
    parser.add_argument("--proxy",    default=None)
    args = parser.parse_args()

    result = visit(args.url, args.visit_id, args.proxy)
    print(json.dumps(result))
    sys.exit(0 if result["status"] == "success" else 1)