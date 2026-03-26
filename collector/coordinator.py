"""
collector/coordinator.py
========================
SSH orchestrator. Starts synchronized tshark captures on both router VMs,
triggers browser visits on the client VM, and pulls pcap files back locally.

Topology:
  [Client VM] ──► [Ingress Router] ──► [Black Box] ──► [Egress Router] ──► [Server]
                       ↑ capture                              ↑ capture

Usage:
  python coordinator.py --mode baseline --urls config/urls.txt --visits 5 --client client1
"""

import argparse
import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import paramiko

from config.infrastructure import (
    BPF_EGRESS, BPF_INGRESS, CLIENT_GROUPS, CLIENTS, EGRESS_ROUTER,
    INGRESS_ROUTER, MAX_CLOCK_DRIFT_MS, PROXY_MAP, SNAPSHOT_LENGTH, URL_BASE,
)


# ── SSH helpers ───────────────────────────────────────────────────────────────

def ssh_connect(host_cfg: dict) -> paramiko.SSHClient:
    """Opens and returns an authenticated SSHClient for a host config dict."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host_cfg["host"],
        username=host_cfg["user"],
        key_filename=str(Path(host_cfg["key_path"]).expanduser()),
        timeout=15,
    )
    return client


def ssh_run(client: paramiko.SSHClient, cmd: str, check=True) -> str:
    """
    Runs a command on the remote host and returns stdout as a string.
    If check=True, raises RuntimeError on non-zero exit code.
    """
    _, stdout, stderr = client.exec_command(cmd)
    exit_code = stdout.channel.recv_exit_status()
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if check and exit_code != 0:
        raise RuntimeError(
            f"Remote command failed (exit {exit_code}):\n"
            f"  cmd: {cmd}\n"
            f"  stderr: {err}"
        )
    return out


def scp_get(client: paramiko.SSHClient, remote_path: str, local_path: Path):
    """Downloads a file from the remote host to local_path via SFTP."""
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with client.open_sftp() as sftp:
        sftp.get(remote_path, str(local_path))


# ── Clock sync ────────────────────────────────────────────────────────────────

def verify_clock_sync(ingress_ssh, egress_ssh, max_drift_ms=MAX_CLOCK_DRIFT_MS):
    """
    Reads chrony offset from both routers and aborts if inter-router
    drift exceeds max_drift_ms. Called before every capture run.
    """
    def get_offset_ms(ssh):
        out = ssh_run(ssh, "chronyc tracking")
        for line in out.splitlines():
            if "System time" in line:
                parts = line.split()
                offset_s = float(parts[3])
                return offset_s * 1000.0
        raise RuntimeError("Could not parse chrony offset")

    ingress_offset_ms = get_offset_ms(ingress_ssh)
    egress_offset_ms  = get_offset_ms(egress_ssh)
    delta_ms = abs(ingress_offset_ms - egress_offset_ms)

    print(f"[clock] ingress offset: {ingress_offset_ms:+.3f} ms")
    print(f"[clock] egress  offset: {egress_offset_ms:+.3f} ms")
    print(f"[clock] inter-router delta: {delta_ms:.3f} ms (threshold: {max_drift_ms} ms)")

    if delta_ms > max_drift_ms:
        raise RuntimeError(
            f"Clock drift too high: {delta_ms:.3f} ms > {max_drift_ms} ms. "
            f"Aborting capture run."
        )


# ── Remote capture ────────────────────────────────────────────────────────────

def start_remote_capture(ssh_client, iface: str, bpf: str,
                          pcap_remote_path: str) -> str:
    log_file = pcap_remote_path.replace('.pcap', '.log')
    cmd = (
        f"/usr/bin/tshark -i {iface} -f '{bpf}' "
        f"-s {SNAPSHOT_LENGTH} "
        f"-w {pcap_remote_path} "
        f"> {log_file} 2>&1 </dev/null & echo $!"
    )
    pid = ssh_run(ssh_client, cmd)
    time.sleep(1.0)
    check = ssh_run(ssh_client,
                    f"kill -0 {pid} 2>/dev/null && echo alive || echo dead",
                    check=False)
    if "alive" not in check:
        log = ssh_run(ssh_client, f"cat {log_file} 2>/dev/null || echo no log", check=False)
        raise RuntimeError(f"tshark failed to start on {iface}:\n{log}")
    return pid


def stop_remote_capture(ssh_client, pid: str):
    """Sends SIGTERM to the tshark process and waits for it to flush."""
    ssh_run(ssh_client, f"kill {pid}", check=False)
    time.sleep(2.0)


# ── Visit trigger ─────────────────────────────────────────────────────────────

def trigger_visit(client_ssh, url: str, proxy: str | None,
                  visit_id: str, mode: str) -> dict:
    """
    Calls visit_trigger.py on the client VM via SSH.
    Returns metadata dict parsed from stdout JSON.
    """
    proxy_arg = f"--proxy {proxy}" if proxy else ""
    cmd = (
        f"python3 ~/visit_trigger.py "
        f"--url {url} "
        f"--visit_id {visit_id} "
        f"--mode {mode} "
        f"{proxy_arg}"
    )
    out = ssh_run(client_ssh, cmd, check=False)
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"visit_id": visit_id, "url": url, "status": f"parse_error: {out}"}


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
    ingress_pcap:    str
    egress_pcap:     str


def run_single_visit(url: str, mode: str,
                     ingress_ssh, egress_ssh, client_ssh,
                     output_dir: Path,
                     visit_id: str) -> VisitRecord:          # ← accepts visit_id
    """
    Full lifecycle for one visit:
      1. Start ingress + egress captures simultaneously (threaded)
      2. Trigger browser visit on client
      3. Stop captures, pull pcaps, cleanup remote files
      4. Return VisitRecord for logging
    """
    ingress_remote = f"{INGRESS_ROUTER['capture_dir']}/{visit_id}_ingress.pcap"
    egress_remote  = f"{EGRESS_ROUTER['capture_dir']}/{visit_id}_egress.pcap"
    ingress_local  = output_dir / mode / f"{visit_id}_ingress.pcap"
    egress_local   = output_dir / mode / f"{visit_id}_egress.pcap"

    bpf_in  = BPF_INGRESS[mode]
    bpf_out = BPF_EGRESS

    # ── Step 1: Start captures on both routers simultaneously ──────────────
    t_capture_start = time.time()
    ingress_pid_box = [None]
    egress_pid_box  = [None]

    def start_ingress():
        ingress_pid_box[0] = start_remote_capture(
            ingress_ssh,
            INGRESS_ROUTER["iface_client"],      # enp7s0
            bpf_in,
            ingress_remote,
        )

    def start_egress():
        egress_pid_box[0] = start_remote_capture(
            egress_ssh,
            EGRESS_ROUTER["iface_server"],       # enp7s0
            bpf_out,
            egress_remote,
        )

    t_in  = threading.Thread(target=start_ingress)
    t_out = threading.Thread(target=start_egress)
    t_in.start(); t_out.start()
    t_in.join();  t_out.join()

    ingress_pid = ingress_pid_box[0]
    egress_pid  = egress_pid_box[0]
    time.sleep(2.0)  # ensure tshark is fully up before triggering visit
    print(f"  [capture] started — ingress PID {ingress_pid}, egress PID {egress_pid}")

    # ── Step 2: Trigger the browser visit ─────────────────────────────────
    proxy = PROXY_MAP.get(mode)
    visit_meta = trigger_visit(client_ssh, url, proxy, visit_id, mode)
    t_visit_start = visit_meta.get("t_start", time.time())
    t_visit_end   = visit_meta.get("t_end",   time.time())
    visit_status  = visit_meta.get("status",  "unknown")

    print(f"  [visit]   {visit_status} — {visit_meta.get('duration_s', '?')}s")
    time.sleep(3.0)  # ensures trailing packets are captured before tshark is killed

    # ── Step 3: Stop captures ─────────────────────────────────────────────
    def stop_ingress():
        stop_remote_capture(ingress_ssh, ingress_pid)  

    def stop_egress():
        stop_remote_capture(egress_ssh, egress_pid)

    s_in  = threading.Thread(target=stop_ingress)
    s_out = threading.Thread(target=stop_egress)
    s_in.start(); s_out.start()
    s_in.join();  s_out.join()
    t_capture_end = time.time()

    # ── Step 4: Pull pcaps locally ────────────────────────────────────────
    scp_get(ingress_ssh, ingress_remote, ingress_local)
    scp_get(egress_ssh,  egress_remote,  egress_local)

    # ── Step 5: Cleanup remote files ──────────────────────────────────────
    ssh_run(ingress_ssh, f"rm -f {ingress_remote}", check=False)
    ssh_run(egress_ssh,  f"rm -f {egress_remote}",  check=False)

    return VisitRecord(
        visit_id        = visit_id,
        url             = url,
        mode            = mode,
        t_capture_start = t_capture_start,
        t_visit_start   = t_visit_start,
        t_visit_end     = t_visit_end,
        t_capture_end   = t_capture_end,
        visit_status    = visit_status,
        ingress_pcap    = str(ingress_local),
        egress_pcap     = str(egress_local),
    )


# ── Dataset runner ────────────────────────────────────────────────────────────

def run_dataset(url_list_path: str, mode: str,
                visits_per_url: int, output_dir: Path,
                client_id: str):                             # ← accepts client_id
    """
    Iterates over URLs × visits, calls run_single_visit for each,
    and appends VisitRecords to a .jsonl metadata log.
    """
    if client_id not in CLIENT_GROUPS.get(mode, []):
        print(f"[coordinator] WARNING: {client_id} is not the standard "
              f"client for mode={mode}. Expected one of "
              f"{CLIENT_GROUPS.get(mode)}.")

    url_base = URL_BASE[mode]
    urls = [url_base + "/" + line.strip()
            for line in Path(url_list_path).read_text().splitlines()
            if line.strip() and not line.startswith("#")]

    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / f"{mode}_visits.jsonl"

    total = len(urls) * visits_per_url
    print(f"[coordinator] mode={mode} client={client_id} "
          f"urls={len(urls)} visits_per_url={visits_per_url} total={total}")

    print("[coordinator] connecting to routers and client...")
    ingress_ssh = ssh_connect(INGRESS_ROUTER)
    egress_ssh  = ssh_connect(EGRESS_ROUTER)
    client_ssh  = ssh_connect(CLIENTS[client_id])

    try:
        verify_clock_sync(ingress_ssh, egress_ssh)

        serial = 0

        for url in urls:
            for visit_num in range(visits_per_url):
                serial += 1
                visit_id = f"{client_id}_v{serial:05d}"
                print(f"[{serial}/{total}] {visit_id} — {url}")

                try:
                    record = run_single_visit(
                        url, mode,
                        ingress_ssh, egress_ssh, client_ssh,
                        output_dir,
                        visit_id=visit_id,
                    )
                    with log_path.open("a") as f:
                        f.write(json.dumps(asdict(record)) + "\n")

                except Exception as e:
                    print(f"  [error] {e} — skipping visit")
                    continue

                time.sleep(2)

        print(f"[coordinator] done. {serial} visits. log: {log_path}")

    finally:
        ingress_ssh.close()
        egress_ssh.close()
        client_ssh.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode",   required=True, choices=["nym", "tor", "vpn", "baseline"])
    parser.add_argument("--urls",   required=True)
    parser.add_argument("--visits", type=int, default=80)
    parser.add_argument("--output", default="./data")
    parser.add_argument("--client", default="client1",
                        choices=list(CLIENTS.keys()))
    args = parser.parse_args()

    run_dataset(args.urls, args.mode, args.visits, Path(args.output), args.client)