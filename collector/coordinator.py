"""
collector/coordinator.py
========================
SSH orchestrator. Starts synchronized tshark captures on both router VMs,
triggers browser visits on the client VM, and pulls pcap files back locally.

Topology:
  [Client VM] ──► [Ingress Router] ──► [Black Box] ──► [Egress Router] ──► [Server]
                       ↑ capture                              ↑ capture

Usage:
  python coordinator.py --mode vpn  --urls config/urls.txt --visits 5 --client vpn-client1
  python coordinator.py --mode tor  --urls config/urls.txt --visits 5 --client tor-client1 --rotate-circuits
  python coordinator.py --mode nym5 --urls config/urls.txt --visits 5 --client nym5-client1 --rotate-circuits
"""

import argparse
import fcntl
import itertools
import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from pathlib import Path

import paramiko

# Workaround for a paramiko 4.0.0 regression: when the local ssh-agent holds a
# certificate-backed key it can't parse into an inner_key (e.g. a Vault-issued
# ED25519-CERT), AgentKey.__getattr__ forwards attribute lookups to
# `self.inner_key` (None) instead of failing gracefully, and auth_handler
# unconditionally reads `key.public_blob` while enumerating agent identities —
# crashing every agent-based connection, including unrelated keys later in the
# list. Returning None here matches what paramiko does for keys it parses
# successfully but that lack cert data, restoring normal fallback behaviour.
_orig_agentkey_getattr = paramiko.agent.AgentKey.__getattr__

def _safe_agentkey_getattr(self, name):
    if self.inner_key is None:
        if name == "public_blob":
            return None
        raise AttributeError(name)
    return _orig_agentkey_getattr(self, name)

paramiko.agent.AgentKey.__getattr__ = _safe_agentkey_getattr

from config.infrastructure import (
    BPF_EGRESS, CLIENT_GROUPS, CLIENTS, EGRESS_ROUTER,
    INGRESS_ROUTER, MAX_CLOCK_DRIFT_MS, PROXY_MAP,
    SNAPSHOT_LENGTH, TOR_CONTROL_PASSWORD, URL_BASE, WEB_SERVER,
    build_ingress_bpf,
)
from config.hyperparams import VISIT_TIMEOUTS


# ── SSH helpers ───────────────────────────────────────────────────────────────
#
# A FULL VM HANG (kernel stops servicing the socket, no RST/FIN ever arrives)
# is invisible to paramiko unless every blocking call carries an explicit
# bound. exec_command(timeout=None) — the default, and what every call below
# used before this was diagnosed — opens its channel via
# Transport.open_session(timeout=None), which falls back to
# Transport.channel_timeout (default 3600s), and then does chan.settimeout
# (None) for the read/write phase, which DISABLES the timeout entirely. A
# hung VM therefore blocks a coordinator with no exception ever raised, which
# is why wedge recovery (keyed on catching an exception) never fired during
# the live nym2/nym5 hangs. CHANNEL_OPEN_TIMEOUT_S is set once at connect
# time so it also backstops open_sftp() (used by scp_get), which has no
# timeout parameter of its own to override the 3600s default.
CHANNEL_OPEN_TIMEOUT_S      = 20    # bounds opening any new channel on this connection
DEFAULT_SSH_EXEC_TIMEOUT_S  = 30    # bounds short administrative commands via ssh_run()
SCP_TIMEOUT_S               = 60    # bounds the pcap-pull data-transfer phase


def ssh_connect(host_cfg: dict) -> paramiko.SSHClient:
    """Opens and returns an authenticated SSHClient for a host config dict."""
    key_path = os.environ.get("SSH_KEY") or host_cfg["key_path"]
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host_cfg["host"],
        username=host_cfg["user"],
        key_filename=str(Path(key_path).expanduser()),
        timeout=15,
        banner_timeout=20,
        auth_timeout=20,
        channel_timeout=CHANNEL_OPEN_TIMEOUT_S,
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


def ssh_run(client: paramiko.SSHClient, cmd: str, check=True,
            timeout: float = DEFAULT_SSH_EXEC_TIMEOUT_S) -> str:
    """
    Runs a command on the remote host and returns stdout as a string.
    If check=True, raises RuntimeError on non-zero exit code.

    timeout bounds the channel-open AND the read phase (paramiko forwards it
    to chan.settimeout(), which Channel.recv()/.read() honor) — a hung VM
    raises socket.timeout/SSHException within `timeout` seconds instead of
    blocking indefinitely. Callers whose remote command can legitimately run
    longer than the default (e.g. trigger_visit's browser/curl visit) must
    pass an explicit larger value.

    recv_exit_status() is called AFTER draining stdout/stderr, not before —
    it has no timeout parameter at all in this paramiko version (raw
    Event.wait(), unbounded) and is also paramiko's own documented advice
    (avoids a window-size deadlock). Calling it first — as this used to —
    means a hung VM blocks forever right there, completely ignoring the
    `timeout` passed to exec_command: confirmed live, on the wire, against a
    real powered-off VM, while validating this fix.
    """
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if not stdout.channel.status_event.wait(timeout):
        raise TimeoutError(
            f"command timed out waiting {timeout}s for exit status: {cmd}"
        )
    exit_code = stdout.channel.exit_status
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
        # open_sftp() has no timeout parameter of its own — its internal
        # channel-open falls back to the connection's channel_timeout
        # (set at connect time). This bounds the actual data-transfer phase,
        # which channel_timeout does not cover.
        sftp.get_channel().settimeout(SCP_TIMEOUT_S)
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


# ── Capture interface detection ───────────────────────────────────────────────

def detect_capture_iface(default_iface: str) -> str:
    """
    Returns the capture interface to use on a remote router.
    Priority: CAPTURE_IFACE env var > default_iface.

    Both routers expose eth0 (public) before enp7s0 (private) in `ip -br
    link show`, so picking "the second link" always resolved to eth0 —
    silently capturing on the wrong interface for every mode. There is no
    reliable way to auto-detect "the capture interface" from link order, so
    this just trusts the configured default unless explicitly overridden.
    """
    return os.environ.get("CAPTURE_IFACE") or default_iface


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
    time.sleep(5.0)


def count_pcap_packets(pcap_path: Path) -> int:
    """
    Returns the packet count in a local pcap file via tshark.

    Used for the zero-ingress guard: a page-load "success" with an empty
    ingress-router pcap is never a real success — the capture point saw
    nothing, regardless of what the browser reported. Returns 0 on any
    failure (missing file, corrupt pcap, tshark error) so callers always
    treat "couldn't verify" the same as "verified empty".
    """
    tshark = shutil.which("tshark") or "/usr/bin/tshark"
    try:
        result = subprocess.run(
            [tshark, "-r", str(pcap_path)],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return 0
    if result.returncode not in (0, 14):  # 14 = truncated final block, still readable
        return 0
    return len([line for line in result.stdout.splitlines() if line.strip()])


# ── Circuit rotation ──────────────────────────────────────────────────────────

_TOR_CONTROL_PORT      = 9051
_NYM_POLL_INTERVAL_S   = 3
_NYM_ROTATE_SLEEP_S      = 30   # time to wait after closing SSH before reconnecting
_NYM_SOCKS5_POLL_TIMEOUT_S = 90  # max wait for SOCKS5 port 1080 to come up after reconnect

# Scripts written to /tmp/nym_rotate.sh on the client VM.
#
# Preamble (both modes): start nym-vpnd if not already running, then wait
# 15 s for the ExecStartPost safe-start hook (nym-vpnd-safe-start.sh) to
# complete.  The hook fires after every `systemctl start` and disconnects any
# auto-connect, deletes the nft table, and restores the default route — so
# after the sleep the daemon is running and SSH is guaranteed accessible.
# The subsequent disconnect/nft-clear/route-restore steps are an extra safety
# net in case nym-vpnd was already running with an active tunnel.
#
# The route-restore line is mode-specific: nym2-client1 has been migrated to
# route its default traffic via enp7s0 (through the ingress router, so the
# Nym outer WireGuard UDP is visible there for capture) while SSH/control
# stays safe via the pre-existing table-100 policy route to eth0 (unaffected
# by this main-table change — verified live). nym5 and nym2-client2 have not
# been migrated yet, so their preamble still restores the original eth0
# default to avoid silently changing untested infrastructure.
#
# nym2 nohup nesting: nym-vpnc connect resets the nftables inet nym table,
# which can kill sshd's session children before post-connect.sh runs.
# Wrapping connect+post-connect in a deeper nohup subshell creates a separate
# process group that survives the nft reset.  sleep 55 (connect ~30s +
# post-connect ~5s + margin 20s) keeps the outer script alive until SSH rules
# are restored; the tun1 check writes a diagnostic line to the log.
#
# SOCKS5 setup is only needed for nym5 (mixnet/5-hop); nym2 uses WireGuard.
_NYM_ROUTE_RESTORE = {
    "eth0":   "ip route replace default via 172.31.1.1 dev eth0 2>/dev/null || true\n",
    "enp7s0": "ip route replace default via 10.0.0.1 dev enp7s0 proto static onlink 2>/dev/null || true\n",
}

def _nym_script_preamble(route_restore: str) -> str:
    return (
        # Ensure daemon is running (safe — ExecStartPost hook clears auto-connect)
        "systemctl start nym-vpnd 2>/dev/null || true\n"
        "sleep 15\n"  # wait for safe-start hook to complete
        # Now safe: daemon running, no active tunnel, SSH accessible
        "nym-vpnc disconnect 2>/dev/null || true\n"
        "sleep 3\n"
        "nft delete table inet nym 2>/dev/null || true\n"
        + _NYM_ROUTE_RESTORE[route_restore]
        + "sleep 2\n"
    )

def _build_nym_rotate_script(socks5: bool, route_restore: str = "eth0") -> str:
    preamble = _nym_script_preamble(route_restore)
    if socks5:
        # nym5: clear existing state, re-enable SOCKS5, connect.
        socks5_block = (
            "nym-vpnc socks5 disable || true\n"
            "sleep 1\n"
            "for i in 1 2 3 4 5; do\n"
            "    nym-vpnc socks5 enable --socks5-address 127.0.0.1:1080 --exit-random && break\n"
            '    echo "socks5 enable attempt $i failed, retrying in 5s..."\n'
            "    sleep 5\n"
            "done\n"
            "sleep 2\n"
        )
        return (
            preamble
            + "sleep 1\n"
            + "nym-vpnc disconnect\n"
            + "sleep 8\n"
            + socks5_block
            + "nym-vpnc connect --wait && /usr/local/bin/nym-post-connect.sh\n"
        )
    else:
        # nym2: preamble ensures daemon is up and clear, then fire
        # connect+post-connect in a nested nohup subshell so the WireGuard
        # nft reset cannot kill this process before post-connect.sh runs.
        # sleep 55 covers connect (~30s) + post-connect (~5s) + margin 20s.
        # The tun1 check writes a diagnostic line to the log.
        return (
            preamble
            + 'nohup bash -c "nym-vpnc connect --wait && /usr/local/bin/nym-post-connect.sh"'
            + " > /tmp/nym_rotate.log 2>&1 &\n"
            + "sleep 55\n"
            + "ip link show tun1 2>/dev/null && echo tun1-UP || echo tun1-DOWN\n"
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
    route_restore: str = "eth0",
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
    # 0 — ensure nym-vpnd daemon is running.
    # The daemon auto-connects on startup (installing nft rules), but starting
    # it here is safe when it is already active.  We must not trigger a fresh
    # auto-connect at this point; the rotate script handles disconnect/reconnect.
    ssh_run(
        client_ssh,
        "systemctl is-active nym-vpnd || systemctl start nym-vpnd",
        check=False,
    )

    # 1 — write rotate script via SFTP (no shell-quoting concerns)
    with client_ssh.open_sftp() as sftp:
        with sftp.file("/tmp/nym_rotate.sh", "w") as fh:
            fh.write(_build_nym_rotate_script(socks5, route_restore))

    # 2 — launch nohup background script
    ssh_run(client_ssh, "nohup bash /tmp/nym_rotate.sh > /tmp/nym_rotate.log 2>&1 &", check=False)

    # 3 — close SSH before disconnect drops nftables rules
    try:
        client_ssh.close()
    except Exception:
        pass

    # 4 — wait for the rotate script to reach a state where SSH is accessible.
    if socks5:
        # nym5: script has no internal wait; coordinator sleeps to cover the
        # full disconnect → connect → post-connect cycle.
        print(f"  [rotate-nym]  nym5 sleeping {_NYM_ROTATE_SLEEP_S}s for reconnect…")
        time.sleep(_NYM_ROTATE_SLEEP_S)
    else:
        # nym2: the rotate script sleeps 40s after firing the inner nohup,
        # which covers the full connect + post-connect window.  Coordinator
        # does not sleep here; retry_ssh_connect handles any remaining wait.
        print(f"  [rotate-nym]  nym2 reconnect handled by script-internal sleep 40")

    # 5 — fresh SSH connection (nftables SSH rules are restored by now)
    new_ssh = retry_ssh_connect(client_cfg)

    # 5b — poll until SOCKS5 port 1080 is listening (nym5 only; nym2 uses WireGuard)
    #
    # The channel can die AGAIN here, after already surviving one reconnect
    # above: the nohup'd rotate script keeps mutating routing/nft state for a
    # while after the first successful reconnect (post-connect.sh's own
    # multi-second sequence, SSH-safety reassertion, etc), and ssh_run raises
    # even with check=False on a dead channel. Confirmed live (2026-07-04):
    # this exact spot crashed the whole coordinator process mid-rotation.
    # Treat a mid-poll disconnect as "reconnect and keep polling", not fatal.
    if socks5:
        poll_start = time.time()
        while True:
            try:
                out = ssh_run(new_ssh, "ss -tnlp | grep 1080 || true", check=False)
            except Exception as e:
                print(f"  [rotate-nym]  ssh dropped mid-poll ({e}) — reconnecting")
                try:
                    new_ssh.close()
                except Exception:
                    pass
                new_ssh = retry_ssh_connect(client_cfg)
                out = ""
            if "1080" in out:
                print(f"  [rotate-nym]  SOCKS5 port 1080 ready ({time.time() - poll_start:.0f}s)")
                break
            if time.time() - poll_start >= _NYM_SOCKS5_POLL_TIMEOUT_S:
                print(f"  [rotate-nym]  WARNING: SOCKS5 port 1080 not ready after {_NYM_SOCKS5_POLL_TIMEOUT_S}s — continuing anyway")
                break
            print(f"  [rotate-nym]  waiting for SOCKS5… ({time.time() - poll_start:.0f}s)")
            time.sleep(_NYM_POLL_INTERVAL_S)

    # 6 — read status from the live tunnel (same dead-channel risk as 5b)
    try:
        status = ssh_run(new_ssh, "nym-vpnc status 2>/dev/null || cat /tmp/nym_rotate.log 2>/dev/null || echo unknown", check=False)
    except Exception:
        try:
            new_ssh.close()
        except Exception:
            pass
        new_ssh = retry_ssh_connect(client_cfg)
        status = ssh_run(new_ssh, "nym-vpnc status 2>/dev/null || echo unknown", check=False)

    # nym-vpnc status format (v1.27):
    #   "State: Connected mix to <ip> [<entry-id>] → <ip> [<exit-id>]"
    entry = "unknown"
    exit_ = "unknown"
    m = re.search(r"Connected.*?\[(\S+)\].*?\[(\S+)\]", status)
    if m:
        entry = m.group(1)
        exit_ = m.group(2)

    circuit_info = f"entry={entry} exit={exit_}"

    try:
        routes = ssh_run(new_ssh, "ip route show table 100 2>/dev/null | head -3", check=False)
    except Exception:
        try:
            new_ssh.close()
        except Exception:
            pass
        new_ssh = retry_ssh_connect(client_cfg)
        routes = "(reconnected after routes check dropped)"
    print(f"  [rotate-nym]  {circuit_info}  routes={routes!r:.80}")
    return circuit_info, new_ssh


# Clients whose default route has been migrated to transit the ingress
# router (table-100 SSH safety verified live first) — see _NYM_ROUTE_RESTORE.
# Only add a client here after independently verifying its table-100/fwmark
# SSH-safety net, same as was done for nym2-client1.
_NYM_CLIENTS_VIA_INGRESS_ROUTER = {"nym2-client1", "nym5-client1", "nym2-client2", "nym5-client2"}


def maybe_rotate_circuit(
    client_ssh: paramiko.SSHClient,
    client_cfg: dict,
    mode: str,
    rotate: bool,
    client_id: str = "",
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
        route_restore = "enp7s0" if client_id in _NYM_CLIENTS_VIA_INGRESS_ROUTER else "eth0"
        return rotate_circuit_nym(client_ssh, client_cfg, socks5=(mode == "nym5"), route_restore=route_restore)
    return "", client_ssh   # vpn: no circuit concept


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
    # visit_trigger.py bounds itself internally (playwright page.goto timeout
    # / curl --max-time) to VISIT_TIMEOUTS[mode]["curl_s"] at most — the SSH
    # layer must allow at least that long plus process/launch overhead, or a
    # legitimately slow (but successful) visit gets misclassified as a hang.
    visit_ssh_timeout = VISIT_TIMEOUTS.get(mode, VISIT_TIMEOUTS["vpn"])["curl_s"] + 60
    out = ssh_run(client_ssh, cmd, check=False, timeout=visit_ssh_timeout)
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

    # Web server responding — curl run on egress router (shares 10.1.x.x subnet
    # with server). Deliberately NOT URL_BASE[mode]: for tor/nym5/nym2,
    # URL_BASE points at the egress router's OWN public IP (it's the address
    # real visits hit from outside) — curling that from the egress router
    # itself is a self/hairpin connection that fails outright on this
    # infra (observed: curl exits non-zero before getting any response, so
    # both the "-w" format string and the "|| echo 000" fallback fire,
    # printing "000000" — never a real wrong-code failure). Checking the web
    # server's private IP directly on the same port nginx actually listens on
    # (see scripts/setup_webserver_ports.sh) tests the exact thing that
    # matters — is nginx serving content on this mode's port — without
    # depending on whether self-hairpin routing happens to work.
    _WEBSERVER_PORT = {"vpn": 8080, "tor": 8081, "nym5": 8082, "nym2": 80}
    check_url = f"http://{WEB_SERVER['private_ip']}:{_WEBSERVER_PORT[mode]}/page_html_1.html"
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


# ── Wedge detection and recovery ───────────────────────────────────────────────
#
# nym-vpnd-correlated full network-stack wedges (both public and private IP
# unreachable) were observed repeatedly during live testing — nym2-client1 four
# times, both nym5 clients independently. Root cause undiagnosed; this section
# is about surviving it during an unattended run, not preventing it.
#
# Two tiers, cheapest first:
#   "soft"  — SSH still reachable but nym-vpnd/tunnel state is broken (no tun
#             interface, SOCKS5 not listening, nym-vpnc status unresponsive).
#             Recovered by restarting nym-vpnd and re-applying the route.
#   "hard"  — SSH unreachable on both public and private IP (the VM's own
#             networking is wedged). Recovered via the Hetzner API
#             (`hcloud server reset`), independent of the broken network path.

WEDGE_MAX_RECOVERY_ATTEMPTS = 2
_HARD_WEDGE_REBOOT_WAIT_S   = 240   # max time to wait for SSH after hcloud reset
_SOFT_WEDGE_SSH_RETRIES     = 3
_SOFT_WEDGE_SSH_RETRY_DELAY = 10
# nym5's SOCKS5 bring-up after any nym-vpnd (re)start is genuinely slow —
# observed taking up to ~78s in live testing (post-connect.conf ExecStartPost
# hook + nym-vpnc negotiation). A recovery action that merely confirms SSH is
# back and then checks health once after a short fixed sleep burns a whole
# WEDGE_MAX_RECOVERY_ATTEMPTS slot on a client that was actually fine, just
# not finished booting — poll instead of single-shot-checking.
_WEDGE_HEALTH_POLL_TIMEOUT_S  = 90
_WEDGE_HEALTH_POLL_INTERVAL_S = 5
# Nym-specific connection-level reconnect attempts (Tier 1a in recover_wedged_client).
# The common trigger for nym mass-hangs is a transient gateway lookup failure
# ("Failed to lookup gateways with SOCKS5 data: failed to get gateways") which
# leaves nym-vpnd running but SOCKS5 in State:Disabled / no tun. nym-vpnc
# reconnect fixes this without a VM reset. Retry a couple of times with a short
# gap — the gateway service may still be recovering — before falling through to
# service restart (Tier 1b) or hard reset (Tier 2).
_NYM_RECONNECT_RETRIES       = 2
_NYM_RECONNECT_RETRY_DELAY_S = 15
# Tier 1a used to run `nym-vpnc reconnect` directly over the existing SSH
# channel. Confirmed live (2026-07-04, canary nym5-client2) that reconnect
# itself transiently breaks SSH — it rebuilds routing/nft state and the
# in-flight channel dies mid-command, so the follow-up `socks5 enable` and
# the health poll then run against a dead channel and never recover, hanging
# tier 1a instead of escalating. Fix mirrors rotate_circuit_nym's already-
# working pattern: write the reconnect+reassert steps to a script, launch it
# nohup'd so it survives the channel dying, close this channel proactively,
# then open a fresh SSH connection rather than trust the old one.
_NYM_TIER1A_RECONNECT_WAIT_S = 40   # reconnect (~30s) + post-connect.sh (~5s) + margin
# hcloud per-server lock + retry-on-locked constants.
# "resource is locked" is a transient Hetzner API state (action already in
# flight on that server). Retry with exponential backoff instead of failing;
# use a per-server flock file to prevent two callers from ever firing
# overlapping ops on the same VM (distinct VMs are independent and parallel).
_HCLOUD_RESET_MAX_LOCKED_RETRIES  = 8
_HCLOUD_RESET_LOCKED_BACKOFF_BASE = 15    # seconds; doubles per retry, capped at:
_HCLOUD_RESET_LOCKED_BACKOFF_MAX  = 120   # seconds
_HCLOUD_RESET_CMD_TIMEOUT         = 120   # per-attempt subprocess timeout (s)


def _poll_until_healthy(ssh: paramiko.SSHClient, mode: str,
                         timeout_s: int = _WEDGE_HEALTH_POLL_TIMEOUT_S,
                         interval_s: int = _WEDGE_HEALTH_POLL_INTERVAL_S) -> tuple[bool, str]:
    """Polls check_client_health() until it passes or timeout_s elapses."""
    deadline = time.time() + timeout_s
    healthy, reason = check_client_health(ssh, mode)
    while not healthy and time.time() < deadline:
        time.sleep(interval_s)
        healthy, reason = check_client_health(ssh, mode)
    return healthy, reason


# ── Threshold alerting ──────────────────────────────────────────────────────────
#
# Auto-recovery (above) already handles routine wedges silently — a wedge that
# recovers cleanly is working as intended and must NOT alert, or every normal
# run would spam whoever's watching. Alerts are anchored to FAILURE only:
#   1. any recovery attempt that returns recovered=False
#   2. a client producing zero successful visits for ZERO_SUCCESS_ALERT_WINDOW_S
#   3. a mode's success rate over the last SUCCESS_RATE_ALERT_WINDOW_N visits
#      dropping below SUCCESS_RATE_ALERT_THRESHOLD
#
# Channel: a dedicated ALERTS.log file in the output directory (trivial to
# `tail -f` during an unattended run) plus, if ALERT_WEBHOOK_URL is set in the
# environment, a best-effort POST to it. No mail/Slack/webhook is actually
# configured on this infra at the time of writing (checked: no `mail`/
# `sendmail`, no webhook env vars, nothing in the repo) — the env var hook
# exists so one can be wired in later without touching this code.
ZERO_SUCCESS_ALERT_WINDOW_S    = 1800   # 30 min — config knob
SUCCESS_RATE_ALERT_WINDOW_N    = 10     # visits — config knob
SUCCESS_RATE_ALERT_THRESHOLD   = 0.5    # config knob


def fire_alert(output_dir: Path, message: str) -> None:
    """
    Writes a timestamped ALERT line to <output_dir>/ALERTS.log, prints it
    loudly to stdout, and best-effort POSTs to ALERT_WEBHOOK_URL if set.
    Never raises — an alerting failure must not take down the collection run.
    """
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] ALERT: {message}"
    print(f"\n{'!' * 70}\n{line}\n{'!' * 70}\n")
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        with (output_dir / "ALERTS.log").open("a") as f:
            f.write(line + "\n")
    except Exception as e:
        print(f"  [alert] failed to write ALERTS.log: {e}")

    webhook = os.environ.get("ALERT_WEBHOOK_URL")
    if webhook:
        try:
            subprocess.run(
                ["curl", "-s", "-m", "10", "-X", "POST", webhook,
                 "-H", "Content-Type: application/json",
                 "-d", json.dumps({"text": line})],
                capture_output=True, timeout=15,
            )
        except Exception as e:
            print(f"  [alert] webhook POST failed: {e}")


# ── Mid-visit SOCKS5 wedge classification ───────────────────────────────────
#
# Preflight wedge detection (check_client_health) runs BEFORE run_single_visit
# starts a capture/rotation/Playwright cycle — it catches a SOCKS5 listener
# that's already down. But a listener that's healthy at preflight time can
# still die mid-visit, between the preflight check and Playwright's
# page.goto(). When that happens, Playwright surfaces it as a normal (non-
# exception) page-load error string, not an SSH-level failure — so without
# this classification, run_single_visit just returns a VisitRecord with that
# error baked into visit_status, and the caller's wedge-aware loop treats any
# non-exception return as final, permanently losing the visit instead of
# requeuing it like a preflight-caught wedge.
#
# Classification is based on actual error strings observed in a live 20h
# validation run (data/validation_run_20260625_0136/nym5_visits.jsonl):
#   - "NS_ERROR_PROXY_CONNECTION_REFUSED": 108/646 nym5 visits (~17%). Firefox
#     couldn't reach the configured SOCKS5 proxy at 127.0.0.1:1080 at all —
#     this is unambiguously "the local SOCKS5 listener is down/wedged", the
#     exact condition check_client_health's nym5 branch already detects.
#     Confirmed wedge-class: every occurrence had thousands of ingress
#     packets already captured (the zero-ingress guard never fires for
#     these), meaning the *previous* rotation succeeded and the listener
#     died after that — not a dead target.
#   - "NS_ERROR_CONNECTION_REFUSED" (without PROXY_): only 3/646, and each one
#     shows substantial ingress (981-1837 packets) AND non-trivial egress
#     bytes — the proxy was up and routing; this specific request was refused
#     at the destination/relay layer. Deliberately NOT classified as
#     wedge-class: requeuing these indefinitely would be retrying a failure
#     that has nothing to do with SOCKS5 health, exactly the "genuine
#     dead-site error" case that must not be requeued forever.
_SOCKS5_WEDGE_ERROR_MARKERS = ("NS_ERROR_PROXY_CONNECTION_REFUSED",)


def is_socks5_wedge_error(visit_status: str) -> bool:
    return any(marker in visit_status for marker in _SOCKS5_WEDGE_ERROR_MARKERS)


class SOCKS5WedgeError(RuntimeError):
    """Raised by run_single_visit when the page load failed with a SOCKS5-
    wedge-class error — lets the wedge-aware caller loop requeue it exactly
    like a preflight-detected wedge, instead of losing it as a terminal
    error."""


# ── VM-hang classification ──────────────────────────────────────────────────
#
# A full VM hang (kernel stops servicing the socket) now surfaces as a bounded
# timeout exception instead of blocking forever (see ssh_run/ssh_connect
# above). This is rarer and more severe than a routine soft wedge (which
# fire_alert deliberately stays silent on when recovery succeeds — see the
# "Threshold alerting" section), so for an unattended multi-day run it is
# always logged to ALERTS.log, detection AND outcome, regardless of whether
# recovery succeeds — that's the trail this run needs that didn't exist
# before.
_VM_HANG_REASON_MARKERS = (
    "timed out",                 # socket.timeout str()
    "Timeout opening channel",   # paramiko SSHException on a hung channel-open
    "Connection timed out",
    "No route to host",
    "Connection refused",        # transient during a reboot, but reconnect-side
)


def is_vm_hang_reason(reason: str) -> bool:
    return any(marker in reason for marker in _VM_HANG_REASON_MARKERS)


def check_client_health(client_ssh: paramiko.SSHClient, mode: str) -> tuple[bool, str]:
    """
    Returns (healthy, reason). reason is "" when healthy, else a short
    description of what was observed — used as the wedge record's cause.

    Checks, in order of how much they imply:
      1. SSH transport is alive and a trivial command completes within a
         short timeout — a hard (full network-stack) wedge fails here.
      2. Mode-specific tunnel signal — a soft (nym-vpnd/tunnel-only) wedge
         fails here while SSH itself is fine.

    The whole body is one try/except, not just step 1: the mode-specific
    ssh_run() calls below can also raise (TimeoutError/SSHException) even
    with check=False — that only suppresses non-zero exit codes, not a
    channel that dies between here and the previous check. Originally only
    step 1 was guarded, so a channel dying during step 2 raised uncaught out
    of this function into whichever tier's polling loop called it, crashing
    the entire coordinator process for that client with no fallback —
    confirmed live (2026-07-04, nym5-client2's coordinator process vanished
    exactly this way, traceback rooted in this function via _poll_until_healthy).
    """
    try:
        if not (client_ssh.get_transport() and client_ssh.get_transport().is_active()):
            return False, "ssh transport not active"
        _, stdout, _ = client_ssh.exec_command("echo ALIVE", timeout=10)
        out = stdout.read().decode().strip()
        stdout.channel.recv_exit_status()
        if out != "ALIVE":
            return False, f"ssh command did not echo ALIVE (got {out!r})"

        if mode == "nym5":
            socks5 = ssh_run(client_ssh, "ss -tnlp 2>/dev/null | grep 1080 || true", check=False)
            if "1080" not in socks5:
                return False, "nym5 SOCKS5 port 1080 not listening"
        elif mode == "nym2":
            tun_check = ssh_run(client_ssh, "ip link show tun1 2>/dev/null || true", check=False)
            if "tun1" not in tun_check:
                return False, "nym2 tun1 interface not present"

        if mode in ("nym2", "nym5"):
            status = ssh_run(client_ssh, "timeout 8 nym-vpnc status 2>&1 || echo TIMEOUT", check=False)
            if "TIMEOUT" in status or "Failed to create RPC client" in status:
                return False, f"nym-vpnc status unresponsive ({status.strip()[:80]!r})"
    except Exception as e:
        return False, f"ssh unreachable: {e}"

    return True, ""


def _hcloud_reset(client_id: str) -> tuple[bool, str]:
    """
    Submit `hcloud server reset <client_id>` with two reliability layers:

    1. Per-server exclusive file lock (/tmp/hcloud_reset_{id}.lock, fcntl LOCK_EX)
       so no two callers ever fire overlapping resets on the same VM. The lock
       is held only for the duration of the hcloud subprocess call — distinct
       VMs lock independently and reset in parallel. The same lock file is used
       by run_stage.sh's ensure_client_reachable (via bash flock) so bash and
       Python callers are mutually exclusive on the same server.

    2. Retry on "resource is locked" — a transient Hetzner API state meaning an
       op is already in progress on this server (a peer may have fired a reset,
       or the user ran one manually). We back off and retry instead of failing,
       because the lock will clear once the in-flight action completes.

    Returns (ok: bool, fail_reason: str).
    """
    hcloud = shutil.which("hcloud") or "hcloud"
    lock_path = Path(f"/tmp/hcloud_reset_{client_id}.lock")
    lock_path.touch(exist_ok=True)

    delay = _HCLOUD_RESET_LOCKED_BACKOFF_BASE
    for attempt in range(1, _HCLOUD_RESET_MAX_LOCKED_RETRIES + 1):
        # Acquire LOCK_EX only for the subprocess call; released on fd close
        # so other VMs (different lock files) proceed in parallel.
        with open(lock_path, "a") as lock_fh:
            fcntl.flock(lock_fh, fcntl.LOCK_EX)
            try:
                result = subprocess.run(
                    [hcloud, "server", "reset", client_id],
                    capture_output=True, text=True,
                    timeout=_HCLOUD_RESET_CMD_TIMEOUT,
                )
            except subprocess.TimeoutExpired:
                return (False,
                        f"hcloud_reset_timeout_after_{_HCLOUD_RESET_CMD_TIMEOUT}s")
            except Exception as e:
                return False, f"hcloud_reset_exception: {e}"
            # lock_fh.__exit__ closes fd → LOCK_EX released here

        if result.returncode == 0:
            return True, ""

        stderr = result.stderr.strip()
        if "locked" in stderr.lower():
            if attempt >= _HCLOUD_RESET_MAX_LOCKED_RETRIES:
                return (False,
                        f"hcloud_reset_still_locked_after_{attempt}_retries: "
                        f"{stderr[:200]}")
            print(f"  [hcloud] {client_id}: resource locked "
                  f"(attempt {attempt}/{_HCLOUD_RESET_MAX_LOCKED_RETRIES})"
                  f" — retrying in {delay}s")
            time.sleep(delay)
            delay = min(delay * 2, _HCLOUD_RESET_LOCKED_BACKOFF_MAX)
        else:
            return (False,
                    f"hcloud_reset_failed (rc={result.returncode}): {stderr[:200]}")

    return False, "hcloud_reset_max_retries_exceeded"  # not reachable


def recover_wedged_client(client_id: str, client_cfg: dict, mode: str) -> tuple[bool, str, paramiko.SSHClient | None]:
    """
    Three-tier recovery escalation. Each tier is only reached if the previous
    one failed — hcloud reset is the last resort, not the first.

    Tier 1a — nym connection reconnect (nym5/nym2 only, SSH reachable,
      nym-vpnd active): the common mass-hang trigger is a transient nym
      gateway lookup failure that leaves nym-vpnd running but SOCKS5 in
      State:Disabled / no tun. nym-vpnc reconnect + socks5 enable fixes it
      without touching the VM or the service. Retried _NYM_RECONNECT_RETRIES
      times with a short gap (gateway service may still be recovering).

    Tier 1b — service restart (SSH reachable, all modes): nym-vpnd crashed or
      Tier 1a exhausted. systemctl restart nym-vpnd + route reapplication.
      Slower than reconnect but doesn't need an out-of-band API call.

    Tier 2 — hcloud reset (SSH unreachable or both soft tiers failed):
      _hcloud_reset() serializes per-server and retries on "resource is locked"
      so concurrent recovery attempts on the same VM don't collide.

    Returns (recovered, method, new_client_ssh). new_client_ssh is None if
    recovery failed — callers must not assume the old client_ssh is usable.
    """
    host = client_cfg["host"]

    # ── Attempt SSH connection ─────────────────────────────────────────────
    ssh = None
    for attempt in range(_SOFT_WEDGE_SSH_RETRIES):
        try:
            ssh = ssh_connect(client_cfg)
            break
        except Exception:
            if attempt < _SOFT_WEDGE_SSH_RETRIES - 1:
                time.sleep(_SOFT_WEDGE_SSH_RETRY_DELAY)

    if ssh is not None:
        # ── Tier 1a: nym-vpnc reconnect (nym5/nym2, nym-vpnd active) ──────────
        # Skip for vpn/tor — they have no nym-vpnc, and check_client_health
        # for those modes only tests SSH echo, which is already up here.
        if mode in ("nym5", "nym2"):
            for conn_attempt in range(1, _NYM_RECONNECT_RETRIES + 1):
                # ssh_run can raise (TimeoutError/SSHException) even with
                # check=False — that only suppresses non-zero exit codes, not
                # a dead channel. Uncaught here, this used to crash the whole
                # coordinator process for this client with no fallback to
                # Tier 1b/2 at all — confirmed live (2026-07-04, nym5-client2
                # vanished mid-recovery with an unhandled TimeoutError
                # traceback). Treat it as "ssh is gone", not a bug to raise.
                try:
                    vpnd_active = "active" == ssh_run(
                        ssh,
                        "systemctl is-active nym-vpnd 2>/dev/null || echo inactive",
                        check=False,
                    ).strip()
                except Exception as e:
                    print(f"  [wedge-recovery] {client_id}: ssh died checking "
                          f"nym-vpnd status ({e}) — falling through to service restart")
                    ssh = None
                    break
                if not vpnd_active:
                    print(f"  [wedge-recovery] {client_id}: nym-vpnd not active "
                          f"— skipping reconnect, going to service restart")
                    break
                print(f"  [wedge-recovery] {client_id}: nym-vpnd active but "
                      f"SOCKS5/tun down — nym-vpnc reconnect "
                      f"(attempt {conn_attempt}/{_NYM_RECONNECT_RETRIES})")
                # nohup'd + reassert-and-reopen pattern (see comment on
                # _NYM_TIER1A_RECONNECT_WAIT_S): reconnect is dispatched to a
                # detached background script so it survives its own channel
                # dying, and SSH-safety is reasserted unconditionally inside
                # that same script rather than over a channel that may
                # already be gone by the time a follow-up command is sent.
                socks5_line = (
                    "nym-vpnc socks5 enable --socks5-address 127.0.0.1:1080 "
                    "--exit-random 2>/dev/null || true\n" if mode == "nym5" else ""
                )
                tier1a_script = (
                    "nym-vpnc reconnect 2>/dev/null || true\n"
                    + socks5_line
                    + "/usr/local/bin/nym-post-connect.sh 2>/dev/null || true\n"
                )
                try:
                    sftp = ssh.open_sftp()
                    with sftp.file("/tmp/nym_tier1a_reconnect.sh", "w") as fh:
                        fh.write(tier1a_script)
                    sftp.close()
                    ssh_run(ssh, "nohup bash /tmp/nym_tier1a_reconnect.sh "
                                 "> /tmp/nym_tier1a_reconnect.log 2>&1 &", check=False)
                except Exception as e:
                    print(f"  [wedge-recovery] {client_id}: failed to dispatch "
                          f"reconnect script: {e}")
                try:
                    ssh.close()
                except Exception:
                    pass
                time.sleep(_NYM_TIER1A_RECONNECT_WAIT_S)
                try:
                    ssh = ssh_connect(client_cfg)
                except Exception as e:
                    print(f"  [wedge-recovery] {client_id}: SSH did not come back "
                          f"after reconnect dispatch: {e}")
                    ssh = None
                    break
                healthy, reason = _poll_until_healthy(ssh, mode)
                if healthy:
                    return True, "soft_nym_reconnect", ssh
                print(f"  [wedge-recovery] {client_id}: reconnect attempt "
                      f"{conn_attempt}/{_NYM_RECONNECT_RETRIES} failed ({reason})"
                      + (" — retrying" if conn_attempt < _NYM_RECONNECT_RETRIES
                         else " — falling through to service restart"))
                if conn_attempt < _NYM_RECONNECT_RETRIES:
                    time.sleep(_NYM_RECONNECT_RETRY_DELAY_S)

        # ── Tier 1b: service restart (all modes) ──────────────────────────────
        # ssh can be None here if Tier 1a's post-reconnect SSH reopen failed
        # (see the `ssh = None; break` above) — one more reconnect attempt
        # before giving up on soft recovery entirely and escalating to hcloud.
        if ssh is None:
            try:
                ssh = ssh_connect(client_cfg)
            except Exception:
                ssh = None
        if ssh is None:
            print(f"  [wedge-recovery] {client_id}: SSH unreachable — "
                  f"escalating to hcloud reset")
        else:
            print(f"  [wedge-recovery] {client_id}: restarting nym-vpnd service")
            try:
                ssh_run(ssh, "systemctl restart nym-vpnd", check=False)
                if client_id in _NYM_CLIENTS_VIA_INGRESS_ROUTER:
                    ssh_run(ssh, "ip route replace default via 10.0.0.1 dev enp7s0 "
                                 "proto static onlink", check=False)
                healthy, reason = _poll_until_healthy(ssh, mode)
                if healthy:
                    return True, "soft_restart_nym_vpnd", ssh
                print(f"  [wedge-recovery] {client_id}: still unhealthy after service "
                      f"restart ({reason}) — escalating to hcloud reset")
            except Exception as e:
                print(f"  [wedge-recovery] {client_id}: service restart failed: {e} "
                      f"— escalating to hcloud reset")
        try:
            ssh.close()
        except Exception:
            pass

    # ── Tier 2: hcloud reset — SSH unreachable or soft recovery exhausted ──────
    print(f"  [wedge-recovery] {client_id}: attempting hcloud reset (host={host})")
    ok, fail_reason = _hcloud_reset(client_id)
    if not ok:
        return False, fail_reason, None

    deadline = time.time() + _HARD_WEDGE_REBOOT_WAIT_S
    ssh = None
    while time.time() < deadline:
        try:
            ssh = ssh_connect(client_cfg)
            print(f"  [wedge-recovery] {client_id}: SSH back after hcloud reset")
            break
        except Exception:
            time.sleep(10)

    if ssh is None:
        return False, "hcloud_reset_ssh_never_returned", None

    # SSH back is not the same as healthy — nym-vpnd needs to (re)start and,
    # for nym5, SOCKS5 needs to come up (observed up to ~78s after a fresh
    # boot). Poll rather than declaring victory the moment SSH answers, or
    # the very next preflight check in the caller's loop fails immediately
    # and burns a recovery attempt on a client that just needed more time.
    healthy, reason = _poll_until_healthy(ssh, mode)
    if healthy:
        return True, "hard_hcloud_reset", ssh
    print(f"  [wedge-recovery] {client_id}: SSH back but still unhealthy after "
          f"hcloud reset ({reason})")
    try:
        ssh.close()
    except Exception:
        pass
    return False, f"hcloud_reset_unhealthy_after_reboot: {reason}", None


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
    ingress_packets: int = 0    # zero-ingress guard: see count_pcap_packets()
    circuit_info:    str = ""   # guard/gateway logged after circuit rotation
    tun1_ip:         str = ""   # nym2 only: dynamic tun1 IP used for BPF + direction
    backfill:        bool = False


def run_single_visit(url: str, mode: str,
                     ingress_ssh, egress_ssh, client_ssh,
                     output_dir: Path,
                     visit_id: str,
                     client_id: str,
                     rotate_circuits: bool = False,
                     client_cfg: dict | None = None,
                     backfill: bool = False) -> VisitRecord:
    """
    Full lifecycle for one visit:
      0. (Optional) Rotate circuit — NEWNYM for Tor, reconnect for Nym
      1. Start ingress + egress captures simultaneously (threaded)
      2. Trigger browser visit on client
      3. Stop captures, pull pcaps, cleanup remote files
      4. Raise SOCKS5WedgeError if the page load failed with a proxy-refused
         error (mid-visit SOCKS5 wedge) — the caller's wedge-aware loop
         catches this and requeues, same as a preflight-detected wedge
      5. Return VisitRecord for logging

    pcaps are written under a per-client subdirectory of capture_dir so that
    parallel coordinators sharing the same routers never race on filenames.
    """
    ingress_remote = f"{INGRESS_ROUTER['capture_dir']}/{client_id}/{visit_id}_ingress.pcap"
    egress_remote  = f"{EGRESS_ROUTER['capture_dir']}/{client_id}/{visit_id}_egress.pcap"
    ingress_local  = output_dir / mode / f"{visit_id}_ingress.pcap"
    egress_local   = output_dir / mode / f"{visit_id}_egress.pcap"

    bpf_in  = build_ingress_bpf(mode, client_id)
    bpf_out = BPF_EGRESS[mode]
    tun1_ip = ""

    # ── Step 0: Rotate circuit (Tor NEWNYM / Nym reconnect) ───────────────
    # For nym modes, rotate_circuit_nym closes client_ssh and returns a new one.
    circuit_info, client_ssh = maybe_rotate_circuit(
        client_ssh, client_cfg or {}, mode, rotate_circuits, client_id
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

    # ── Steps 1-4: capture / trigger / stop / pull, retried once on a ZERO
    # INGRESS pcap ────────────────────────────────────────────────────────
    # A page-load "success" with an empty ingress-router pcap is never a
    # real success — every mode is expected to produce ingress packets
    # (EGRESS_ONLY_MODES is empty). One retry of the whole capture cycle
    # covers transient capture-start timing glitches; if it's still zero
    # after that, the visit is marked ZERO_INGRESS and must not be counted
    # as success — do NOT retry forever, and do NOT silently accept it.
    ZERO_INGRESS_MAX_RETRIES = 1
    ingress_packet_count = 0
    visit_status = "unknown"

    for capture_attempt in range(ZERO_INGRESS_MAX_RETRIES + 1):
        t_capture_start = time.time()
        ingress_pid_box = [None]
        egress_pid_box  = [None]
        ingress_err_box = [None]
        egress_err_box  = [None]

        def start_ingress():
            # nym2 may use a dedicated ingress interface (eth0 vs enp7s0) depending
            # on whether WireGuard outer UDP exits via the public or private NIC.
            iface = (
                INGRESS_ROUTER.get("iface_nym2_ingress", INGRESS_ROUTER["iface_client"])
                if mode == "nym2"
                else INGRESS_ROUTER["iface_client"]
            )
            try:
                ingress_pid_box[0] = start_remote_capture(
                    ingress_ssh,
                    iface,
                    bpf_in,
                    ingress_remote,
                )
            except Exception as e:
                # Was `except RuntimeError` only — a bounded-timeout
                # exception (socket.timeout / SSHException) from ssh_run is
                # neither, so it used to die silently inside this thread:
                # the thread exits, join() returns normally, and the
                # caller's err-box check below never fires. Router-side
                # failures here are intentionally NOT routed into client
                # wedge-recovery (rebooting the client VM doesn't fix a
                # hung/misconfigured router) — they still just skip this
                # visit, but now visibly instead of vanishing.
                ingress_err_box[0] = e

        def start_egress():
            try:
                egress_pid_box[0] = start_remote_capture(
                    egress_ssh,
                    EGRESS_ROUTER["iface_server"],       # enp7s0
                    bpf_out,
                    egress_remote,
                )
            except Exception as e:
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

        # ── Trigger the browser visit ─────────────────────────────────────
        # No blind same-process retry here on PROXY_CONNECTION_REFUSED: a
        # proxy refusing connections mid-visit means the SOCKS5 listener
        # itself is down, and retrying without restarting nym-vpnd just
        # fails again. See the SOCKS5-wedge-class check below, which raises
        # instead so the caller's wedge-aware loop (preflight checks use the
        # same path) can actually fix it — restart nym-vpnd, poll for
        # health, requeue this same visit_id — before trying again.
        proxy = PROXY_MAP.get(mode)
        visit_meta = trigger_visit(client_ssh, url, proxy, visit_id, mode)
        t_visit_start = visit_meta.get("t_start", time.time())
        t_visit_end   = visit_meta.get("t_end",   time.time())
        visit_status  = visit_meta.get("status",  "unknown")

        print(f"  [visit]   {visit_status} — {visit_meta.get('duration_s', '?')}s")
        time.sleep(3.0)  # ensures trailing packets are captured before tshark is killed

        # ── Stop captures ──────────────────────────────────────────────────
        # Best-effort cleanup (router-side, not part of client wedge
        # detection) — but an uncaught exception inside a Thread target
        # dies silently (join() returns normally either way), so a bounded-
        # timeout exception from a hung router would otherwise vanish
        # without a trace. Catch and log instead of letting it disappear.
        def stop_ingress():
            try:
                stop_remote_capture(ingress_ssh, ingress_pid)
            except Exception as e:
                print(f"  [warn] stop_remote_capture(ingress) failed: {e}")

        def stop_egress():
            try:
                stop_remote_capture(egress_ssh, egress_pid)
            except Exception as e:
                print(f"  [warn] stop_remote_capture(egress) failed: {e}")

        s_in  = threading.Thread(target=stop_ingress)
        s_out = threading.Thread(target=stop_egress)
        s_in.start(); s_out.start()
        s_in.join();  s_out.join()
        t_capture_end = time.time()

        # ── Pull pcaps locally (retry once on transient SCP failure) ──────
        scp_get_with_retry(ingress_ssh, ingress_remote, ingress_local)
        scp_get_with_retry(egress_ssh,  egress_remote,  egress_local)

        ssh_run(ingress_ssh, f"rm -f {ingress_remote}", check=False)
        ssh_run(egress_ssh,  f"rm -f {egress_remote}",  check=False)

        # ── ZERO-INGRESS GUARD ─────────────────────────────────────────────
        ingress_packet_count = count_pcap_packets(ingress_local)
        if ingress_packet_count > 0:
            break
        if capture_attempt < ZERO_INGRESS_MAX_RETRIES:
            print(f"  [zero-ingress] 0 packets in ingress pcap (visit_status={visit_status!r}) "
                  f"— retrying capture once before marking failed")
        else:
            print(f"  [zero-ingress] still 0 packets after retry — marking ZERO_INGRESS "
                  f"(page-load status was {visit_status!r})")
            visit_status = "ZERO_INGRESS"

    # ── Release collection lock (nym modes only) ───────────────────────────
    if mode in ("nym5", "nym2"):
        ssh_run(client_ssh, "rm -f /tmp/nym_collection_active", check=False)

    # ── Mid-visit SOCKS5-wedge check ────────────────────────────────────────
    # Capture/pcap-pull/lock-release above already ran to completion — no
    # resources are leaked by raising now instead of returning. Raising
    # (rather than returning a VisitRecord with a bad status) is what lets
    # the caller's wedge-aware loop treat this exactly like a preflight-
    # detected wedge: restart nym-vpnd, poll for health, requeue this same
    # visit_id, bounded by the same WEDGE_MAX_RECOVERY_ATTEMPTS.
    if is_socks5_wedge_error(visit_status):
        raise SOCKS5WedgeError(visit_status.splitlines()[0])

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
        ingress_packets = ingress_packet_count,
        circuit_info    = circuit_info,
        tun1_ip         = tun1_ip,
        backfill        = backfill,
    )


# ── Dataset runner ────────────────────────────────────────────────────────────

def run_dataset(url_list_path: str, mode: str,
                visits_per_url: int, output_dir: Path,
                client_id: str,
                rotate_circuits: bool = False,
                rotate_every: int = 1,
                backfill_urls_path: str | None = None,
                backfill_stop_file: str | None = None):
    """
    Iterates over URLs × visits, calls run_single_visit for each,
    and appends VisitRecords to a .jsonl metadata log.

    rotate_every: rotate the circuit only on visit serials 1, N+1, 2N+1, ...
    (1-indexed, so the very first visit always gets a fresh circuit); the
    N-1 visits in between reuse whatever circuit is already up. serial is
    the absolute per-client visit counter (not reset per-URL), so the
    rotation cadence stays correct across a resumed run. Wedge recovery is
    unaffected either way: it restarts nym-vpnd / reconnects independently
    of this schedule whenever check_client_health fails, regardless of
    whether this particular visit was due to rotate.
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

    # Resolve capture interfaces at session start: respects CAPTURE_IFACE env
    # var, then auto-detects from the router (NR==2 in `ip -br link show`),
    # then falls back to the config default.
    resolved_in_iface = detect_capture_iface(INGRESS_ROUTER["iface_client"])
    resolved_eg_iface = detect_capture_iface(EGRESS_ROUTER["iface_server"])
    if resolved_in_iface != INGRESS_ROUTER["iface_client"]:
        print(f"[coordinator] ingress iface: {INGRESS_ROUTER['iface_client']} → {resolved_in_iface}")
        INGRESS_ROUTER["iface_client"] = resolved_in_iface
    else:
        print(f"[coordinator] ingress iface: {resolved_in_iface}")
    if resolved_eg_iface != EGRESS_ROUTER["iface_server"]:
        print(f"[coordinator] egress  iface: {EGRESS_ROUTER['iface_server']} → {resolved_eg_iface}")
        EGRESS_ROUTER["iface_server"] = resolved_eg_iface
    else:
        print(f"[coordinator] egress  iface: {resolved_eg_iface}")
    if mode == "nym2" and "iface_nym2_ingress" in INGRESS_ROUTER:
        print(f"[coordinator] nym2 ingress iface override: {INGRESS_ROUTER['iface_nym2_ingress']}")

    # Per-client capture subdirectory: avoids filename races when multiple
    # coordinators run in parallel against the same routers.
    ingress_capture_subdir = f"{INGRESS_ROUTER['capture_dir']}/{client_id}"
    egress_capture_subdir  = f"{EGRESS_ROUTER['capture_dir']}/{client_id}"
    ssh_run(ingress_ssh, f"mkdir -p {ingress_capture_subdir}", check=False)
    ssh_run(egress_ssh,  f"mkdir -p {egress_capture_subdir}",  check=False)

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
                        elif "_bf" in vid:
                            serial = max(serial, int(vid.split("_bf")[-1]))
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
        status_counts: dict[str, int] = defaultdict(int)
        wedge_events: list[dict] = []   # one entry per detected wedge, recovered or not

        # ── Threshold-alerting state ────────────────────────────────────────
        last_success_time = time.time()
        zero_success_alert_active = False   # avoid re-alerting every visit while still breached
        recent_outcomes: deque[bool] = deque(maxlen=SUCCESS_RATE_ALERT_WINDOW_N)
        success_rate_alert_active = False

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
                should_rotate = rotate_circuits and ((serial - 1) % rotate_every == 0)
                print(f"[{overall}/{total}] {visit_id} — {url}"
                      + ("" if should_rotate or not rotate_circuits
                         else f"  [circuit] reusing (rotate-every={rotate_every}, "
                              f"due at serial {((serial - 1) // rotate_every) * rotate_every + 1})"))

                if overall % 50 == 0:
                    print(f"[coordinator] periodic clock sync check at visit {overall}...")
                    verify_clock_sync(ingress_ssh, egress_ssh)  # aborts run on drift

                # ── Wedge-aware visit attempt loop ─────────────────────────
                # A wedged client must never be logged as "success" or
                # silently skipped: detect it (pre-flight health check, or
                # an exception raised mid-visit), attempt bounded recovery,
                # and requeue the SAME visit_id. Only after
                # WEDGE_MAX_RECOVERY_ATTEMPTS failed recoveries does the
                # visit get marked WEDGE_UNRECOVERABLE and the loop moves on.
                visit_attempt  = 0
                visit_succeeded = False
                while True:
                    visit_attempt += 1

                    healthy = False
                    wedge_reason = None
                    if not (client_ssh.get_transport() and client_ssh.get_transport().is_active()):
                        # A dead transport here means SSH is unreachable right
                        # now — exactly what the wedge loop below exists to
                        # handle. retry_ssh_connect() raises after its own
                        # bounded attempts; that must become a wedge_reason,
                        # not an uncaught exception that kills the whole run.
                        try:
                            client_ssh = retry_ssh_connect(CLIENTS[client_id])
                        except Exception as e:
                            wedge_reason = f"reconnect failed: {e}"

                    if wedge_reason is None:
                        healthy, reason = check_client_health(client_ssh, mode)
                        wedge_reason = None if healthy else f"preflight: {reason}"

                    if healthy:
                        try:
                            record = run_single_visit(
                                url, mode,
                                ingress_ssh, egress_ssh, client_ssh,
                                output_dir,
                                visit_id=visit_id,
                                client_id=client_id,
                                rotate_circuits=should_rotate,
                                client_cfg=CLIENTS[client_id],
                            )
                            with log_path.open("a") as f:
                                f.write(json.dumps(asdict(record)) + "\n")
                            status_counts[record.visit_status] += 1
                            visit_succeeded = record.visit_status == "success"
                            break  # visit succeeded (whatever its status) — done
                        except Exception as e:
                            wedge_reason = f"mid-visit exception: {e}"

                    vm_hang = wedge_reason is not None and is_vm_hang_reason(wedge_reason)
                    print(f"  [wedge] {client_id}: {wedge_reason} "
                          f"(attempt {visit_attempt}/{WEDGE_MAX_RECOVERY_ATTEMPTS + 1})"
                          + ("  *** VM-HANG ***" if vm_hang else ""))
                    if vm_hang:
                        # Unlike routine soft wedges (silent on success — see
                        # the "Threshold alerting" header comment), a full
                        # VM hang is rare and severe enough that an
                        # unattended multi-day run needs a trail of every
                        # occurrence, not just failures.
                        print(f"  [wedge] VM-hang detected on {client_id} ({mode}) "
                              f"=> attempting recovery (reason: {wedge_reason})")
                        fire_alert(
                            output_dir,
                            f"VM-hang detected on {client_id} ({mode}) => "
                            f"attempting recovery (reason: {wedge_reason})",
                        )

                    if visit_attempt > WEDGE_MAX_RECOVERY_ATTEMPTS:
                        print(f"  [wedge] {client_id}: giving up after "
                              f"{WEDGE_MAX_RECOVERY_ATTEMPTS} recovery attempts — "
                              f"marking WEDGE_UNRECOVERABLE (visit {visit_id} lost)")
                        status_counts["WEDGE_UNRECOVERABLE"] += 1
                        wedge_events.append({
                            "client_id": client_id, "visit_id": visit_id,
                            "detected_at": time.time(), "reason": wedge_reason,
                            "recovery_method": None, "recovered": False,
                        })
                        with log_path.open("a") as f:
                            f.write(json.dumps({
                                "visit_id": visit_id, "url": url, "mode": mode,
                                "visit_status": "WEDGE_UNRECOVERABLE",
                                "wedge_reason": wedge_reason,
                            }) + "\n")
                        visit_succeeded = False
                        break

                    recovered, method, new_ssh = recover_wedged_client(
                        client_id, CLIENTS[client_id], mode
                    )
                    wedge_events.append({
                        "client_id": client_id, "visit_id": visit_id,
                        "detected_at": time.time(), "reason": wedge_reason,
                        "recovery_method": method, "recovered": recovered,
                    })
                    if recovered and new_ssh is not None:
                        try:
                            client_ssh.close()
                        except Exception:
                            pass
                        client_ssh = new_ssh
                        print(f"  [wedge] {client_id}: recovered via {method} — "
                              f"requeuing visit {visit_id}")
                        if vm_hang:
                            # Outcome trail for the VM-hang case specifically
                            # — logged even on success, unlike routine wedges.
                            fire_alert(
                                output_dir,
                                f"VM-hang on {client_id} ({mode}): recovery "
                                f"SUCCEEDED via {method} — resuming visit {visit_id}",
                            )
                    else:
                        print(f"  [wedge] {client_id}: recovery attempt failed "
                              f"({method}) — retrying")
                        # Alert condition 1: a recovery attempt that failed.
                        # A wedge that recovers cleanly must NOT alert — only
                        # this branch (recovered=False) does (VM-hang success
                        # is the one exception, handled above).
                        fire_alert(
                            output_dir,
                            f"{'VM-hang on ' if vm_hang else ''}{client_id} ({mode}): "
                            f"recovery attempt failed (method={method}) for visit "
                            f"{visit_id} — reason: {wedge_reason}",
                        )

                # ── Threshold alerts 2 & 3: evaluated once per completed visit
                # (success, failure, or WEDGE_UNRECOVERABLE) ────────────────
                now = time.time()
                if visit_succeeded:
                    last_success_time = now
                    zero_success_alert_active = False
                elif now - last_success_time > ZERO_SUCCESS_ALERT_WINDOW_S:
                    if not zero_success_alert_active:
                        fire_alert(
                            output_dir,
                            f"{client_id} ({mode}): zero successful visits for "
                            f"over {ZERO_SUCCESS_ALERT_WINDOW_S}s "
                            f"(last success: {time.strftime('%H:%M:%S', time.localtime(last_success_time))})",
                        )
                        zero_success_alert_active = True

                recent_outcomes.append(visit_succeeded)
                if len(recent_outcomes) >= SUCCESS_RATE_ALERT_WINDOW_N:
                    rate = sum(recent_outcomes) / len(recent_outcomes)
                    if rate < SUCCESS_RATE_ALERT_THRESHOLD:
                        if not success_rate_alert_active:
                            fire_alert(
                                output_dir,
                                f"{client_id} ({mode}): success rate over last "
                                f"{len(recent_outcomes)} visits is {rate:.0%}, "
                                f"below threshold {SUCCESS_RATE_ALERT_THRESHOLD:.0%}",
                            )
                            success_rate_alert_active = True
                    else:
                        success_rate_alert_active = False
                    # loop back: requeue the same visit_id either way, up to
                    # the bounded attempt count above

                time.sleep(2)

        print(f"[coordinator] done. {serial} visits. log: {log_path}")
        print(f"[coordinator] status breakdown: "
              f"{dict(sorted(status_counts.items(), key=lambda kv: -kv[1]))}")
        zero_ingress_n = status_counts.get("ZERO_INGRESS", 0)
        if zero_ingress_n:
            print(f"[coordinator] *** {zero_ingress_n} visit(s) marked ZERO_INGRESS — "
                  f"ingress-router pcap was empty. Check client-side route/tcpdump state. ***")
        if wedge_events:
            recovered_n   = sum(1 for w in wedge_events if w["recovered"])
            unrecovered_n = len(wedge_events) - recovered_n
            lost_visits   = sorted({w["visit_id"] for w in wedge_events
                                     if not w["recovered"]})
            print(f"[coordinator] *** wedge events: {len(wedge_events)} total "
                  f"({recovered_n} recovered, {unrecovered_n} not) on client "
                  f"{client_id} ***")
            for w in wedge_events:
                ts = time.strftime("%H:%M:%S", time.localtime(w["detected_at"]))
                print(f"    [{ts}] {w['visit_id']}: {w['reason']} → "
                      f"method={w['recovery_method']} recovered={w['recovered']}")
            if lost_visits:
                print(f"[coordinator] *** {len(lost_visits)} visit(s) PERMANENTLY "
                      f"LOST to unrecoverable wedges: {lost_visits} ***")
        alerts_log = output_dir / "ALERTS.log"
        if alerts_log.exists():
            print(f"[coordinator] *** alerts were fired during this run — "
                  f"see {alerts_log} ***")

        # ── Backfill loop ──────────────────────────────────────────────────
        # Enabled only when --backfill-urls and --backfill-stop-file are both
        # set (run_stage.sh passes them when BACKFILL=1). Cycles through the
        # shared URLs continuously, stopping as soon as the sentinel file
        # appears (created by the nym5 monitor in run_stage.sh). Uses the same
        # wedge-recovery machinery as the primary loop; visits are tagged
        # backfill=True so the budget tracker and dataset builder can
        # distinguish them.
        if backfill_urls_path and backfill_stop_file:
            bf_stop = Path(backfill_stop_file)
            if bf_stop.exists():
                print(f"[coordinator] backfill: stop file already present — skipping")
            else:
                bf_urls = [URL_BASE[mode] + "/" + l.strip()
                           for l in Path(backfill_urls_path).read_text().splitlines()
                           if l.strip() and not l.startswith("#")]
                if not bf_urls:
                    print(f"[coordinator] backfill: no URLs in {backfill_urls_path!r} — skipping")
                else:
                    print(f"[coordinator] backfill start: cycling {len(bf_urls)} URL(s), "
                          f"stop when {backfill_stop_file!r} exists")
                    bf_count = 0
                    bf_status: dict[str, int] = defaultdict(int)
                    for url in itertools.cycle(bf_urls):
                        if bf_stop.exists():
                            break
                        serial += 1
                        bf_count += 1
                        visit_id = f"{client_id}_bf{serial:05d}"
                        should_rotate = rotate_circuits and ((serial - 1) % rotate_every == 0)
                        print(f"[bf {bf_count}] {visit_id} — {url}")

                        visit_attempt = 0
                        while True:
                            if bf_stop.exists():
                                break
                            visit_attempt += 1
                            healthy = False
                            wedge_reason = None
                            if not (client_ssh.get_transport() and
                                    client_ssh.get_transport().is_active()):
                                try:
                                    client_ssh = retry_ssh_connect(CLIENTS[client_id])
                                except Exception as e:
                                    wedge_reason = f"reconnect failed: {e}"
                            if wedge_reason is None:
                                healthy, reason = check_client_health(client_ssh, mode)
                                wedge_reason = None if healthy else f"preflight: {reason}"
                            if healthy:
                                try:
                                    record = run_single_visit(
                                        url, mode,
                                        ingress_ssh, egress_ssh, client_ssh,
                                        output_dir,
                                        visit_id=visit_id,
                                        client_id=client_id,
                                        rotate_circuits=should_rotate,
                                        client_cfg=CLIENTS[client_id],
                                        backfill=True,
                                    )
                                    with log_path.open("a") as f:
                                        f.write(json.dumps(asdict(record)) + "\n")
                                    bf_status[record.visit_status] += 1
                                    break
                                except Exception as e:
                                    wedge_reason = f"mid-visit exception: {e}"
                            if visit_attempt > WEDGE_MAX_RECOVERY_ATTEMPTS:
                                bf_status["WEDGE_UNRECOVERABLE"] += 1
                                with log_path.open("a") as f:
                                    f.write(json.dumps({
                                        "visit_id": visit_id, "url": url, "mode": mode,
                                        "visit_status": "WEDGE_UNRECOVERABLE",
                                        "backfill": True,
                                        "wedge_reason": wedge_reason,
                                    }) + "\n")
                                break
                            recovered, method, new_ssh = recover_wedged_client(
                                client_id, CLIENTS[client_id], mode
                            )
                            if recovered and new_ssh is not None:
                                try:
                                    client_ssh.close()
                                except Exception:
                                    pass
                                client_ssh = new_ssh
                            else:
                                fire_alert(
                                    output_dir,
                                    f"{client_id} ({mode}) backfill: recovery attempt "
                                    f"failed (method={method}) for visit {visit_id}",
                                )
                        time.sleep(2)
                    print(f"[coordinator] backfill done: {bf_count} extra visits — "
                          f"{dict(sorted(bf_status.items(), key=lambda kv: -kv[1]))}")

    finally:
        ssh_run(ingress_ssh, f"rm -rf {ingress_capture_subdir}", check=False)
        ssh_run(egress_ssh,  f"rm -rf {egress_capture_subdir}",  check=False)
        ingress_ssh.close()
        egress_ssh.close()
        client_ssh.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode",            required=True,
                        choices=["tor", "vpn", "nym5", "nym2"])
    parser.add_argument("--urls",            required=True)
    parser.add_argument("--visits",          type=int, default=80)
    parser.add_argument("--output",          default="./data")
    parser.add_argument("--client",          default="vpn-client1",
                        choices=list(CLIENTS.keys()))
    parser.add_argument("--rotate-circuits", action="store_true", default=False,
                        help="Rotate Tor circuit (NEWNYM) or Nym gateway every --rotate-every visits")
    parser.add_argument("--rotate-every",    type=int, default=1,
                        help="Rotate only every Nth visit (default 1 = every visit, "
                             "same as before). Visits in between reuse the existing "
                             "circuit — no reconnect, no rotation sleep.")
    parser.add_argument("--backfill-urls",      default=None,
                        help="Path to file of bare URL paths to cycle through after "
                             "primary collection finishes (enables backfill). "
                             "Requires --backfill-stop-file.")
    parser.add_argument("--backfill-stop-file", default=None,
                        help="Sentinel file path; backfill stops when this file exists "
                             "(created by the nym5 monitor in run_stage.sh).")
    args = parser.parse_args()
    if args.rotate_every < 1:
        parser.error("--rotate-every must be >= 1")

    run_dataset(args.urls, args.mode, args.visits, Path(args.output), args.client,
                rotate_circuits=args.rotate_circuits, rotate_every=args.rotate_every,
                backfill_urls_path=args.backfill_urls,
                backfill_stop_file=args.backfill_stop_file)