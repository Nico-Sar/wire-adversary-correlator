"""
collector/visit_trigger.py
==========================
Runs on the CLIENT VM. Called by coordinator.py via SSH.
Launches a headless Firefox browser visit (HTML/JSON) or curl (binary files)
and prints metadata as JSON to stdout.

Deploy to ~/visit_trigger.py on each client VM.

Usage (called by coordinator, not directly):
  python3 visit_trigger.py --url example.com --visit_id abc123 [--proxy socks5://...]
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

# Binary extensions handled by curl, not browser
BINARY_EXTENSIONS = {'.pdf', '.zip', '.mp3', '.mp4', '.bin', '.zip'}


def visit_curl(url: str, visit_id: str, proxy: str | None) -> dict:
    """
    Downloads a binary resource via curl.
    Used for PDF, ZIP, MP3, MP4 — avoids browser download dialogs.
    """
    full_url = f"http://{url}" if not url.startswith("http") else url
    cmd = [
        'curl', '-s', '-o', '/dev/null',
        '--max-time', '120',
    ]
    if proxy:
        cmd += ['--proxy', proxy]
    cmd.append(full_url)

    t_start = time.time()
    result = subprocess.run(cmd, capture_output=True)
    t_end = time.time()

    status = 'success' if result.returncode == 0 else f'curl_error: {result.returncode}'
    return {
        'visit_id':   visit_id,
        'url':        url,
        'proxy':      proxy,
        't_start':    t_start,
        't_end':      t_end,
        'duration_s': round(t_end - t_start, 3),
        'status':     status,
    }


def visit_browser(url: str, visit_id: str, proxy: str | None) -> dict:
    """
    Launches a headless Firefox browser, navigates to the URL,
    waits for page load + settle time, then closes.
    """
    full_url = f"http://{url}" if not url.startswith("http") else url
    meta = {
        'visit_id':   visit_id,
        'url':        url,
        'proxy':      proxy,
        't_start':    time.time(),
        't_end':      None,
        'duration_s': None,
        'status':     'started',
    }

    try:
        with sync_playwright() as p:
            launch_kwargs = {}
            if proxy:
                launch_kwargs['proxy'] = {'server': proxy}

            browser = p.firefox.launch(headless=True, **launch_kwargs)
            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                ignore_https_errors=True,
            )
            page = context.new_page()

            try:
                page.goto(full_url, wait_until='load', timeout=30000)
                time.sleep(3)   # settle time for trailing requests
                meta['status'] = 'success'
            except Exception as e:
                meta['status'] = f'error: {e}'
            finally:
                browser.close()

    except Exception as e:
        meta['status'] = f'launch_error: {e}'

    meta['t_end']      = time.time()
    meta['duration_s'] = round(meta['t_end'] - meta['t_start'], 3)
    return meta


def visit(url: str, visit_id: str, proxy: str | None) -> dict:
    """Routes to curl or browser based on file extension."""
    ext = Path(url.split('?')[0]).suffix.lower()
    if ext in BINARY_EXTENSIONS:
        return visit_curl(url, visit_id, proxy)
    else:
        return visit_browser(url, visit_id, proxy)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url',      required=True)
    parser.add_argument('--visit_id', required=True)
    parser.add_argument('--proxy',    default=None)
    args = parser.parse_args()

    result = visit(args.url, args.visit_id, args.proxy)
    print(json.dumps(result))
    sys.exit(0 if result['status'] == 'success' else 1)