"""
collector/coordinator.py
========================
SSH orchestrator. Starts synchronized tshark captures on both router VMs,
triggers browser visits on the client VM, and pulls pcap files back locally.

Topology:
  [Client VM] ──► [Ingress Router] ──► [Black Box] ──► [Egress Router] ──► [Server]
                       ↑ capture                              ↑ capture

Usage:
  python coordinator.py --mode nym --urls ../config/urls.txt --visits 80
"""

import argparse
import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import paramiko

from config.infrastructure import (
    BPF_EGRESS, BPF_INGRESS, CLIENTS, EGRESS_ROUTER,
    INGRESS_ROUTER, MAX_CLOCK_DRIFT_MS, PROXY_MAP, SNAPSHOT_LENGTH,
)


# ── SSH helpers ───────────────────────────────────────────────────────────────

def ssh_connect(host_cfg: dict) -> paramiko.SSHClient:
    raise NotImplementedError


def ssh_run(client: paramiko.SSHClient, cmd: str, check=True) -> str:
    raise NotImplementedError


def scp_get(client: paramiko.SSHClient, remote_path: str, local_path: Path):
    raise NotImplementedError


# ── Clock sync ────────────────────────────────────────────────────────────────

def verify_clock_sync(ingress_ssh, egress_ssh, max_drift_ms=MAX_CLOCK_DRIFT_MS):
    """
    Reads chrony offset from both routers and aborts if inter-router
    drift exceeds max_drift_ms. Called before every capture run.
    """
    raise NotImplementedError


# ── Remote capture ────────────────────────────────────────────────────────────

def start_remote_capture(ssh_client, iface: str, bpf: str,
                          pcap_remote_path: str) -> str:
    """Starts tshark on the remote router. Returns the process PID."""
    raise NotImplementedError


def stop_remote_capture(ssh_client, pid: str):
    raise NotImplementedError


# ── Visit trigger ─────────────────────────────────────────────────────────────

def trigger_visit(client_ssh, url: str, proxy: str | None,
                  visit_id: str) -> dict:
    """
    Calls visit_trigger.py on the client VM via SSH.
    Returns metadata dict parsed from stdout JSON.
    """
    raise NotImplementedError


# ── Core orchestration ────────────────────────────────────────────────────────

@dataclass
class VisitRecord:
    visit_id:        str
    url:             str
    mode:            str
    t_capture_start: float
    t_visit_start:   float
    t_visit_end:     float
    t_capture_end:   float
    visit_status:    str
    ingress_pcap:    str    # Local path after scp
    egress_pcap:     str    # Local path after scp


def run_single_visit(url: str, mode: str,
                     ingress_ssh, egress_ssh, client_ssh,
                     output_dir: Path) -> VisitRecord:
    """
    Full lifecycle for one visit:
      1. Start ingress + egress captures simultaneously (threaded)
      2. Trigger browser visit on client
      3. Stop captures, pull pcaps, cleanup remote files
      4. Return VisitRecord for logging
    """
    raise NotImplementedError


# ── Dataset runner ────────────────────────────────────────────────────────────

def run_dataset(url_list_path: str, mode: str,
                visits_per_url: int, output_dir: Path):
    """
    Iterates over URLs × visits, calls run_single_visit for each,
    and appends VisitRecords to a .jsonl metadata log.
    """
    raise NotImplementedError


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode",   required=True, choices=["nym", "tor", "vpn", "baseline"])
    parser.add_argument("--urls",   required=True)
    parser.add_argument("--visits", type=int, default=80)
    parser.add_argument("--output", default="./data")
    args = parser.parse_args()

    run_dataset(args.urls, args.mode, args.visits, Path(args.output))
