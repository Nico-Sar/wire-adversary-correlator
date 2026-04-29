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
  python coordinator.py --mode tor      --urls config/urls.txt --visits 5 --client tor-client1 --rotate-circuits
  python coordinator.py --mode nym5     --urls config/urls.txt --visits 5 --client nym5-client1 --rotate-circuits
"""

import argparse
import json
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import paramiko

from config.infrastructure import (
    BPF_EGRESS, BPF_INGRESS, CLIENT_GROUPS, CLIENTS, EGRESS_ROUTER,
    INGRESS_ROUTER, MAX_CLOCK_DRIFT_MS, NYM_EXIT_GATEWAY_ID, PROXY_MAP,
    SNAPSHOT_LENGTH, TOR_CONTROL_PASSWORD, URL_BASE,
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


def retry_ssh_connect(host_cfg: dict, max_retries: int = 5, delay: int = 15) -> paramiko.SSHClient:
    """ssh_connect with retry loop. Sleeps delay seconds between attempts."""
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            return ssh_connect(host_cfg)
        except Exception as e:
            last_exc = e
            if attempt < max_retries:
                print(f"  [ssh] connect to {host_cfg['host']} failed (attempt {attempt}/{max_retries}): {e} — retrying in {delay}s")
                time.sleep(delay)
    raise RuntimeError(
        f"SSH connect to {host_cfg['host']} failed after {max_retries} attempts"
    ) from last_exc


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


def scp_get_with_retry(client: paramiko.SSHClient, remote_path: str,
                       local_path: Path, retries: int = 1, delay: int = 5):
    """Downloads a file, retrying once after `delay` seconds on transient failure."""
    for attempt in range(retries + 1):
        try:
            scp_get(client, remote_path, local_path)
            return
        except Exception as e:
            if attempt < retries:
                print(f"  [scp] pull failed (attempt {attempt + 1}/{retries + 1}): {e}"
                      f" — retrying in {delay}s")
                time.sleep(delay)
            else:
                raise


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


# ── Circuit rotation ──────────────────────────────────────────────────────────

_TOR_CONTROL_PORT        = 9051
_NYM_RECONNECT_TIMEOUT_S = 60
_NYM_POLL_INTERVAL_S     = 3
_NYM_ROTATE_SLEEP_S      = 30   # time to wait after closing SSH before reconnecting
_NYM_SOCKS5_POLL_TIMEOUT_S = 90  # max wait for SOCKS5 port 1080 to come up after reconnect

# Script written to /tmp/nym_rotate.sh on the client VM.
# sleep 1 gives the SSH launcher time to close the connection before disconnect
# drops the nftables rules; nym-vpnc connect then fires and nym-post-connect.sh
# re-adds the SSH-preservation rules so the fresh connection on reconnect works.
# SOCKS5 setup is only needed for nym5 (mixnet/5-hop); nym2 uses WireGuard
# which routes all traffic at the OS level — no SOCKS5 proxy required.
def _build_nym_rotate_script(socks5: bool) -> str:
    socks5_block = (
        "nym-vpnc socks5 disable || true\n"
        "sleep 1\n"
        "for i in 1 2 3 4 5; do\n"
        "    nym-vpnc socks5 enable --socks5-address 127.0.0.1:1080 --exit-random && break\n"
        '    echo "socks5 enable attempt $i failed, retrying in 5s..."\n'
        "    sleep 5\n"
        "done\n"
        "sleep 2\n"
    ) if socks5 else ""
    return (
        "sleep 1\n"
        "nym-vpnc disconnect\n"
        "sleep 8\n"
        + socks5_block
        + "nym-vpnc connect --wait && /usr/local/bin/nym-post-connect.sh\n"
    )


def rotate_circuit_tor(client_ssh) -> str:
    """
    Sends SIGNAL NEWNYM to the Tor control port then reads the first
    entry-guard nickname. Uses two plain nc commands rather than an
    inline python3 script to avoid shell-string escaping issues.

    GETINFO entry-guards response format:
      250+entry-guards=
      $FINGERPRINT~NickName up guard ...
      .
      250 OK
    The nickname is the token after '~' on the first guard line.
    """
    # Send NEWNYM; sleep 1 keeps stdin open until nc flushes the response.
    ssh_run(
        client_ssh,
        f"(printf 'AUTHENTICATE \"{TOR_CONTROL_PASSWORD}\"\\r\\nSIGNAL NEWNYM\\r\\n';"
        f" sleep 1) | nc -q 1 127.0.0.1 {_TOR_CONTROL_PORT}",
        check=False,
    )

    # Wait for the new circuit to establish before querying guards.
    time.sleep(5)

    # Query entry-guards; sleep 2 keeps stdin open long enough to receive
    # the multi-line reply before nc closes the connection.
    out = ssh_run(
        client_ssh,
        f"(printf 'AUTHENTICATE \"{TOR_CONTROL_PASSWORD}\"\\r\\nGETINFO entry-guards\\r\\n';"
        f" sleep 2) | nc -q 1 127.0.0.1 {_TOR_CONTROL_PORT}",
        check=False,
    )

    nickname = "unknown"
    for line in out.splitlines():
        m = re.search(r'\$[0-9A-Fa-f]+~(\S+)', line)
        if m:
            nickname = m.group(1)
            break

    guard = f"guard={nickname}"
    print(f"  [rotate-tor]  {guard}")
    return guard


def rotate_circuit_nym(
    client_ssh: paramiko.SSHClient,
    client_cfg: dict,
    socks5: bool,
) -> tuple[str, paramiko.SSHClient]:
    """
    Rotates the Nym gateway by running a nohup script that survives the SSH
    disconnection caused by nym-vpnc connect resetting the nftables rules.

    socks5=True  → nym5 (mixnet): script includes socks5 disable/enable retry loop.
    socks5=False → nym2 (WireGuard): script skips socks5 entirely; traffic is
                   routed through the tunnel at OS level, no proxy needed.

    Sequence:
      1. Write /tmp/nym_rotate.sh via SFTP (avoids shell-quoting issues).
      2. Launch it as a nohup background process.
      3. Close the SSH connection immediately — before disconnect fires.
      4. Sleep _NYM_ROTATE_SLEEP_S seconds on the coordinator.
      5. Open a fresh SSH connection (nftables SSH rules restored by post-connect).
      6. Read nym-vpnc status and parse entry/exit gateway IDs.

    Returns (circuit_info, new_ssh) where circuit_info is "entry=<id> exit=<id>".
    """
    # 1 — write rotate script via SFTP (no shell-quoting concerns)
    with client_ssh.open_sftp() as sftp:
        with sftp.file("/tmp/nym_rotate.sh", "w") as fh:
            fh.write(_build_nym_rotate_script(socks5))

    # 2 — launch nohup background script
    ssh_run(client_ssh, "nohup bash /tmp/nym_rotate.sh > /tmp/nym_rotate.log 2>&1 &", check=False)

    # 3 — close SSH before disconnect drops nftables rules
    try:
        client_ssh.close()
    except Exception:
        pass

    # 4 — wait for disconnect → connect → post-connect to complete
    print(f"  [rotate-nym]  sleeping {_NYM_ROTATE_SLEEP_S}s for reconnect…")
    time.sleep(_NYM_ROTATE_SLEEP_S)

    # 5 — fresh SSH connection (nftables SSH rules are restored by now)
    new_ssh = retry_ssh_connect(client_cfg)

    # 5b — poll until SOCKS5 port 1080 is listening (nym5 only; nym2 uses WireGuard)
    if socks5:
        poll_start = time.time()
        while True:
            out = ssh_run(new_ssh, "ss -tnlp | grep 1080 || true", check=False)
            if "1080" in out:
                print(f"  [rotate-nym]  SOCKS5 port 1080 ready ({time.time() - poll_start:.0f}s)")
                break
            if time.time() - poll_start >= _NYM_SOCKS5_POLL_TIMEOUT_S:
                print(f"  [rotate-nym]  WARNING: SOCKS5 port 1080 not ready after {_NYM_SOCKS5_POLL_TIMEOUT_S}s — continuing anyway")
                break
            print(f"  [rotate-nym]  waiting for SOCKS5… ({time.time() - poll_start:.0f}s)")
            time.sleep(_NYM_POLL_INTERVAL_S)

    # 6 — read status from the live tunnel
    status = ssh_run(new_ssh, "nym-vpnc status 2>/dev/null || cat /tmp/nym_rotate.log 2>/dev/null || echo unknown", check=False)

    # nym-vpnc status format (v1.27):
    #   "State: Connected mix to <ip> [<entry-id>] → <ip> [<exit-id>]"
    entry = "unknown"
    exit_ = "unknown"
    m = re.search(r"Connected.*?\[(\S+)\].*?\[(\S+)\]", status)
    if m:
        entry = m.group(1)
        exit_ = m.group(2)

    circuit_info = f"entry={entry} exit={exit_}"

    routes = ssh_run(new_ssh, "ip route show table 100 2>/dev/null | head -3", check=False)
    print(f"  [rotate-nym]  {circuit_info}  routes={routes!r:.80}")
    return circuit_info, new_ssh


def maybe_rotate_circuit(
    client_ssh: paramiko.SSHClient,
    client_cfg: dict,
    mode: str,
    rotate: bool,
) -> tuple[str, paramiko.SSHClient]:
    """
    Calls the appropriate rotation function for the given mode.
    Returns (circuit_info, client_ssh) — for nym modes client_ssh is a NEW
    connection opened after the reconnect; for all other modes it is unchanged.
    circuit_info is an empty string when rotation is disabled.
    """
    if not rotate:
        return "", client_ssh
    if mode == "tor":
        return rotate_circuit_tor(client_ssh), client_ssh
    if mode in ("nym5", "nym2"):
        return rotate_circuit_nym(client_ssh, client_cfg, socks5=(mode == "nym5"))
    return "", client_ssh   # baseline / vpn: no circuit concept


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


# ── nym2 tun1 IP helper ───────────────────────────────────────────────────────

def get_nym2_tun_ip(client_ssh) -> str | None:
    """Returns the current tun1 IP on the nym2 client VM, or None if not found."""
    out = ssh_run(
        client_ssh,
        "ip addr show tun1 | grep 'inet ' | awk '{print $2}' | cut -d/ -f1",
        check=False,
    )
    out = out.strip()
    return out if out else None


# ── Pre-flight infrastructure check ──────────────────────────────────────────

def check_infrastructure(mode: str,
                         ingress_ssh:  paramiko.SSHClient,
                         egress_ssh:   paramiko.SSHClient,
                         client_ssh:   paramiko.SSHClient) -> bool:
    """
    Runs pre-flight checks before collection starts.
    Prints PASS/FAIL for each item and returns True if all pass.
    For nym2: if a stale eth0 default route is found it is deleted automatically.
    """
    print("\n[preflight] infrastructure check")
    all_pass = True

    def _check(label: str, ok: bool, detail: str = ""):
        nonlocal all_pass
        tag = "PASS" if ok else "FAIL"
        suffix = f"  ({detail})" if detail else ""
        print(f"  [{tag}] {label}{suffix}")
        if not ok:
            all_pass = False

    # SSH connectivity — already established by the time we're called
    _check("ingress SSH reachable", True, INGRESS_ROUTER["host"])
    _check("egress  SSH reachable", True, EGRESS_ROUTER["host"])

    # tshark present on both routers
    tshark_in = ssh_run(ingress_ssh, "which tshark 2>/dev/null || true", check=False)
    _check("tshark on ingress", bool(tshark_in), tshark_in or "not found")

    tshark_eg = ssh_run(egress_ssh, "which tshark 2>/dev/null || true", check=False)
    _check("tshark on egress", bool(tshark_eg), tshark_eg or "not found")

    # Web server responding — curl run on egress router (shares 10.1.x.x subnet with server)
    check_url = URL_BASE[mode] + "/page_html_1.html"
    http_code = ssh_run(
        egress_ssh,
        f"curl -s -o /dev/null -w '%{{http_code}}' {check_url} 2>/dev/null || echo 000",
        check=False,
    )
    _check("web server responding", http_code == "200", f"HTTP {http_code}  {check_url}")

    # nym2: verify no stale eth0 default route; auto-delete if found
    if mode == "nym2":
        stale = ssh_run(
            client_ssh,
            "ip route show | grep 'default.*eth0' || true",
            check=False,
        ).strip()
        if stale:
            print(f"  [FAIL] nym2 stale eth0 default route: {stale}")
            print(f"  [fix]  deleting stale eth0 default route...")
            ssh_run(
                client_ssh,
                "ip route del default via 172.31.1.1 dev eth0 2>/dev/null || true",
                check=False,
            )
            remaining = ssh_run(
                client_ssh,
                "ip route show | grep 'default.*eth0' || true",
                check=False,
            ).strip()
            if remaining:
                _check("nym2 eth0 default route removed", False, remaining)
            else:
                print("  [fix]  eth0 default route deleted — continuing")
        else:
            _check("nym2 no stale eth0 default route", True)

    # nym5: SOCKS5 proxy port 1080 must be listening
    if mode == "nym5":
        socks5 = ssh_run(client_ssh, "ss -tnlp | grep 1080 || true", check=False)
        _check("nym5 SOCKS5 port 1080 listening",
               "1080" in socks5,
               "listening" if "1080" in socks5 else "not listening")

    if all_pass:
        print("[preflight] all checks passed\n")
    else:
        print("[preflight] WARNING: some checks failed — proceeding anyway\n")
    return all_pass


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
    ingress_bytes:   int = 0
    egress_bytes:    int = 0
    circuit_info:    str = ""   # guard/gateway logged after circuit rotation
    tun1_ip:         str = ""   # nym2 only: dynamic tun1 IP used for BPF + direction


def run_single_visit(url: str, mode: str,
                     ingress_ssh, egress_ssh, client_ssh,
                     output_dir: Path,
                     visit_id: str,
                     rotate_circuits: bool = False,
                     client_cfg: dict | None = None,
                     max_retries: int = 1) -> VisitRecord:
    """
    Full lifecycle for one visit:
      0. (Optional) Rotate circuit — NEWNYM for Tor, reconnect for Nym
      1. Start ingress + egress captures simultaneously (threaded)
      2. Trigger browser visit on client (retried once on PROXY_CONNECTION_REFUSED)
      3. Stop captures, pull pcaps, cleanup remote files
      4. Return VisitRecord for logging
    """
    ingress_remote = f"{INGRESS_ROUTER['capture_dir']}/{visit_id}_ingress.pcap"
    egress_remote  = f"{EGRESS_ROUTER['capture_dir']}/{visit_id}_egress.pcap"
    ingress_local  = output_dir / mode / f"{visit_id}_ingress.pcap"
    egress_local   = output_dir / mode / f"{visit_id}_egress.pcap"

    bpf_in  = BPF_INGRESS[mode]
    bpf_out = BPF_EGRESS[mode]
    tun1_ip = ""

    # ── Step 0: Rotate circuit (Tor NEWNYM / Nym reconnect) ───────────────
    # For nym modes, rotate_circuit_nym closes client_ssh and returns a new one.
    circuit_info, client_ssh = maybe_rotate_circuit(
        client_ssh, client_cfg or {}, mode, rotate_circuits
    )

    # ── Step 0b: Acquire collection lock (nym modes only) ─────────────────
    # Prevents the nym_watchdog.service from reconnecting mid-visit.
    if mode in ("nym5", "nym2"):
        ssh_run(client_ssh, "touch /tmp/nym_collection_active", check=False)

    # ── Step 0c: nym2 — log tun1 IP for debugging ─────────────────────────
    # BPF captures outer WireGuard UDP from the static physical IPs — no
    # dynamic tun1 IP needed for filtering. We still query and log it so
    # post-hoc analysis can correlate gateway assignments with captures.
    if mode == "nym2":
        tun1_ip = get_nym2_tun_ip(client_ssh) or ""
        if tun1_ip:
            print(f"  [nym2]    tun1_ip={tun1_ip}")
        else:
            print(f"  [nym2]    WARNING: could not resolve tun1 IP")

    # ── Step 1: Start captures on both routers simultaneously ──────────────
    t_capture_start = time.time()
    ingress_pid_box = [None]
    egress_pid_box  = [None]
    ingress_err_box = [None]
    egress_err_box  = [None]

    def start_ingress():
        try:
            ingress_pid_box[0] = start_remote_capture(
                ingress_ssh,
                INGRESS_ROUTER["iface_client"],      # enp7s0
                bpf_in,
                ingress_remote,
            )
        except RuntimeError as e:
            ingress_err_box[0] = e

    def start_egress():
        try:
            egress_pid_box[0] = start_remote_capture(
                egress_ssh,
                EGRESS_ROUTER["iface_server"],       # enp7s0
                bpf_out,
                egress_remote,
            )
        except RuntimeError as e:
            egress_err_box[0] = e

    t_in  = threading.Thread(target=start_ingress)
    t_out = threading.Thread(target=start_egress)
    t_in.start(); t_out.start()
    t_in.join();  t_out.join()

    if ingress_err_box[0] or egress_err_box[0]:
        err = ingress_err_box[0] or egress_err_box[0]
        print(f"  [error] tshark failed to start: {err} — skipping")
        for pid, ssh in [(ingress_pid_box[0], ingress_ssh),
                         (egress_pid_box[0],  egress_ssh)]:
            if pid:
                stop_remote_capture(ssh, pid)
        if mode in ("nym5", "nym2"):
            ssh_run(client_ssh, "rm -f /tmp/nym_collection_active", check=False)
        return VisitRecord(
            visit_id        = visit_id,
            url             = url,
            mode            = mode,
            t_capture_start = t_capture_start,
            t_visit_start   = t_capture_start,
            t_visit_end     = t_capture_start,
            t_capture_end   = t_capture_start,
            visit_status    = "skipped_tshark_failed",
            ingress_pcap    = "",
            egress_pcap     = "",
            circuit_info    = circuit_info,
            tun1_ip         = tun1_ip,
        )

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

    for _retry in range(max_retries):
        if "NS_ERROR_PROXY_CONNECTION_REFUSED" not in visit_status:
            break
        print(f"  [retry] PROXY_CONNECTION_REFUSED — waiting 10s and retrying")
        time.sleep(10)
        visit_meta    = trigger_visit(client_ssh, url, proxy, visit_id, mode)
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

    # ── Step 4: Pull pcaps locally (retry once on transient SCP failure) ──
    scp_get_with_retry(ingress_ssh, ingress_remote, ingress_local)
    scp_get_with_retry(egress_ssh,  egress_remote,  egress_local)

    # ── Step 5: Cleanup remote files ──────────────────────────────────────
    ssh_run(ingress_ssh, f"rm -f {ingress_remote}", check=False)
    ssh_run(egress_ssh,  f"rm -f {egress_remote}",  check=False)

    # ── Step 5b: Release collection lock (nym modes only) ─────────────────
    if mode in ("nym5", "nym2"):
        ssh_run(client_ssh, "rm -f /tmp/nym_collection_active", check=False)

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
        ingress_bytes   = ingress_local.stat().st_size,
        egress_bytes    = egress_local.stat().st_size,
        circuit_info    = circuit_info,
        tun1_ip         = tun1_ip,
    )


# ── Dataset runner ────────────────────────────────────────────────────────────

def run_dataset(url_list_path: str, mode: str,
                visits_per_url: int, output_dir: Path,
                client_id: str,
                rotate_circuits: bool = False):
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
    ingress_ssh = retry_ssh_connect(INGRESS_ROUTER)
    egress_ssh  = retry_ssh_connect(EGRESS_ROUTER)
    client_ssh  = retry_ssh_connect(CLIENTS[client_id])

    try:
        verify_clock_sync(ingress_ssh, egress_ssh)
        check_infrastructure(mode, ingress_ssh, egress_ssh, client_ssh)

        # Build per-URL success counts from existing log so a restarted run
        # skips visits that already completed without re-collecting them.
        completed_counts: dict[str, int] = {}  # url → successful visit count
        serial = 0
        if log_path.exists():
            with log_path.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        vid = rec.get("visit_id", "")
                        if "_v" in vid:
                            serial = max(serial, int(vid.split("_v")[-1]))
                        if rec.get("visit_status") == "success":
                            url_key = rec.get("url", "")
                            if url_key:
                                completed_counts[url_key] = completed_counts.get(url_key, 0) + 1
                    except (json.JSONDecodeError, ValueError):
                        continue
            n_done = sum(completed_counts.values())
            if n_done:
                print(f"[coordinator] resuming: {n_done}/{total} visits already collected "
                      f"(max serial={serial})")

        done_total  = sum(completed_counts.values())
        visit_count = 0  # new visits dispatched in this run

        for url in urls:
            for visit_num in range(visits_per_url):
                if visit_num < completed_counts.get(url, 0):
                    url_short = url.split("/")[-1]
                    print(f"  [resume] ({url_short}, visit {visit_num + 1}/{visits_per_url}) "
                          f"already collected — skipping")
                    continue

                serial += 1
                visit_count += 1
                overall   = done_total + visit_count
                visit_id  = f"{client_id}_v{serial:05d}"
                print(f"[{overall}/{total}] {visit_id} — {url}")

                if overall % 50 == 0:
                    print(f"[coordinator] periodic clock sync check at visit {overall}...")
                    verify_clock_sync(ingress_ssh, egress_ssh)  # aborts run on drift

                # Reconnect client if a previous nym rotation closed the transport
                if not (client_ssh.get_transport() and client_ssh.get_transport().is_active()):
                    client_ssh = retry_ssh_connect(CLIENTS[client_id])

                try:
                    record = run_single_visit(
                        url, mode,
                        ingress_ssh, egress_ssh, client_ssh,
                        output_dir,
                        visit_id=visit_id,
                        rotate_circuits=rotate_circuits,
                        client_cfg=CLIENTS[client_id],
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
    parser.add_argument("--mode",            required=True,
                        choices=["baseline", "tor", "vpn", "nym5", "nym2"])
    parser.add_argument("--urls",            required=True)
    parser.add_argument("--visits",          type=int, default=80)
    parser.add_argument("--output",          default="./data")
    parser.add_argument("--client",          default="client1",
                        choices=list(CLIENTS.keys()))
    parser.add_argument("--rotate-circuits", action="store_true", default=False,
                        help="Rotate Tor circuit (NEWNYM) or Nym gateway before each visit")
    args = parser.parse_args()

    run_dataset(args.urls, args.mode, args.visits, Path(args.output), args.client,
                rotate_circuits=args.rotate_circuits)