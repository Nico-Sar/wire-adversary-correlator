"""
collector/visit_trigger.py
==========================
Runs on the CLIENT VM. Called by coordinator.py via SSH.
Launches a headless Firefox browser visit (HTML/JSON) or curl (binary files)
and prints metadata as JSON to stdout.

Deploy to ~/visit_trigger.py on each client VM.

Usage (called by coordinator, not directly):
  python3 visit_trigger.py --url example.com --visit_id abc123 [--proxy socks5://...] [--mode baseline]
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

# Binary extensions handled by curl, not browser
BINARY_EXTENSIONS = {'.pdf', '.zip', '.mp3', '.mp4', '.bin'}

VISIT_TIMEOUTS = {
    "baseline": {"browser_ms": 30_000,  "curl_s": 60},
    "tor":      {"browser_ms": 120_000, "curl_s": 300},
    "vpn":      {"browser_ms": 60_000,  "curl_s": 120},
    "nym5":     {"browser_ms": 180_000, "curl_s": 600},
    "nym2":     {"browser_ms": 120_000, "curl_s": 360},
}


def visit_curl(url: str, visit_id: str, proxy: str | None,
               mode: str = "baseline") -> dict:
    """
    Downloads a binary resource via curl.
    Used for PDF, ZIP, MP3, MP4 — avoids browser download dialogs.
    """
    timeouts = VISIT_TIMEOUTS.get(mode, VISIT_TIMEOUTS["baseline"])
    full_url = f"http://{url}" if not url.startswith("http") else url

    cmd = [
        "curl", "-s", "-o", "/dev/null",
        "--max-time", str(timeouts["curl_s"]),
    ]
    if proxy:
        cmd += ["--proxy", proxy]
    cmd.append(full_url)

    t_start = time.time()
    result = subprocess.run(cmd, capture_output=True)
    t_end = time.time()

    status = "success" if result.returncode == 0 else f"curl_error: {result.returncode}"
    return {
        "visit_id":   visit_id,
        "url":        url,
        "proxy":      proxy,
        "mode":       mode,
        "t_start":    t_start,
        "t_end":      t_end,
        "duration_s": round(t_end - t_start, 3),
        "status":     status,
    }


def visit_browser(url: str, visit_id: str, proxy: str | None,
                  mode: str = "baseline") -> dict:
    """
    Launches a headless Firefox browser, navigates to the URL,
    waits for page load + settle time, then closes.
    """
    timeouts = VISIT_TIMEOUTS.get(mode, VISIT_TIMEOUTS["baseline"])
    full_url = f"http://{url}" if not url.startswith("http") else url

    meta = {
        "visit_id":   visit_id,
        "url":        url,
        "proxy":      proxy,
        "mode":       mode,
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
                page.goto(
                    full_url,
                    wait_until="load",
                    timeout=timeouts["browser_ms"],
                )
                time.sleep(3)   # settle time for trailing requests
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


def visit(url: str, visit_id: str, proxy: str | None,
          mode: str = "baseline") -> dict:
    """Routes to curl or browser based on file extension."""
    ext = Path(url.split("?")[0]).suffix.lower()
    if ext in BINARY_EXTENSIONS:
        return visit_curl(url, visit_id, proxy, mode)
    else:
        return visit_browser(url, visit_id, proxy, mode)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url",      required=True)
    parser.add_argument("--visit_id", required=True)
    parser.add_argument("--proxy",    default=None)
    parser.add_argument("--mode",     default="baseline",
                        choices=["baseline", "tor", "vpn", "nym5", "nym2"])
    args = parser.parse_args()

    result = visit(args.url, args.visit_id, args.proxy, args.mode)
    print(json.dumps(result))
    sys.exit(0 if result["status"] == "success" else 1)