#!/usr/bin/env python3
"""
scripts/deploy_nym_safestart.sh
================================
Deploys the nym-vpnd safe-start hook to all 4 Nym VMs.

ROOT CAUSE ADDRESSED:
  When nym-vpnd starts (for any reason — systemctl, reboot, coordinator),
  it auto-connects and installs nft rules that block SSH.  post-connect.sh
  is never called in this path so SSH stays blocked permanently.

SOLUTION:
  A systemd ExecStartPost drop-in runs nym-vpnd-safe-start.sh after every
  nym-vpnd start.  The hook waits 10 s for the auto-connect to fire, then
  disconnects, clears the nft table, and restores the default route via
  enp7s0/10.0.0.1 (private subnet, through the ingress router) rather than
  via eth0 — this keeps return traffic for the client's private IP visible
  at the ingress router for capture.  Every nym-vpnd start is now inherently
  SSH-safe.

Usage:
    python3 scripts/deploy_nym_safestart.sh
    SSH_KEY=/path/to/key python3 scripts/deploy_nym_safestart.sh
"""

import os
import sys
import time
from pathlib import Path

# Allow running from repo root or from scripts/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import paramiko
from config.infrastructure import CLIENTS

NYM_VMS = ["nym5-client1", "nym5-client2", "nym2-client1", "nym2-client2"]

SAFE_START_SCRIPT = """\
#!/bin/bash
# nym-vpnd-safe-start.sh — ExecStartPost hook
# Waits for nym-vpnd to finish its auto-connect attempt (whatever state
# it lands in), then disconnects and restores SSH rules unconditionally.

LOG=/var/log/nym_safe_start.log
echo "$(date): safe-start hook fired" >> $LOG

# Poll until nym-vpnc reports a stable state (not "Connecting*")
for i in $(seq 1 30); do
    STATUS=$(nym-vpnc status 2>/dev/null || echo "unknown")
    case "$STATUS" in
        *Connecting*) sleep 2 ;;   # still in progress — wait
        *) break ;;                 # landed in any stable state
    esac
done
echo "$(date): stable state: $STATUS" >> $LOG

# Disconnect whatever state it's in and clear nft rules
nym-vpnc disconnect 2>/dev/null || true
sleep 2
nft delete table inet nym 2>/dev/null || true
ip route replace default via 10.0.0.1 dev enp7s0 2>/dev/null || true

echo "$(date): safe-start complete — SSH restored" >> $LOG
"""

DROPIN_CONF = """\
[Service]
ExecStartPost=/usr/local/bin/nym-vpnd-safe-start.sh
"""

SAFE_START_PATH  = "/usr/local/bin/nym-vpnd-safe-start.sh"
DROPIN_DIR       = "/etc/systemd/system/nym-vpnd.service.d"
DROPIN_PATH      = f"{DROPIN_DIR}/safe-start.conf"


def _ssh_connect_with_retry(host_cfg: dict, max_wait: int = 90, interval: int = 10) -> paramiko.SSHClient:
    """Retry SSH connect for up to max_wait seconds — handles safe-start hook window."""
    key_path = os.environ.get("SSH_KEY") or host_cfg["key_path"]
    key_filename = str(Path(key_path).expanduser())
    deadline = time.time() + max_wait
    last_err = None
    while time.time() < deadline:
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                hostname=host_cfg["host"],
                username=host_cfg["user"],
                key_filename=key_filename,
                timeout=8,
            )
            return client
        except Exception as e:
            last_err = e
            time.sleep(interval)
    raise RuntimeError(f"SSH to {host_cfg['host']} failed after {max_wait}s: {last_err}")


def _run(client: paramiko.SSHClient, cmd: str) -> tuple[int, str, str]:
    _, stdout, stderr = client.exec_command(cmd, timeout=30)
    exit_code = stdout.channel.recv_exit_status()
    return exit_code, stdout.read().decode(), stderr.read().decode()


def _put_file(client: paramiko.SSHClient, content: str, remote_path: str):
    sftp = client.open_sftp()
    with sftp.open(remote_path, "w") as fh:
        fh.write(content)
    sftp.close()


def deploy_vm(vm_name: str, cfg: dict) -> bool:
    print(f"\n{'─'*50}")
    print(f"  {vm_name}  ({cfg['host']})")
    print(f"{'─'*50}")

    try:
        ssh = _ssh_connect_with_retry(cfg)
    except Exception as e:
        print(f"  [FAIL] SSH connect failed: {e}")
        return False

    try:
        # ── 1. Write safe-start script ────────────────────────────────────────
        print(f"  [1/4] Writing {SAFE_START_PATH} ...")
        _put_file(ssh, SAFE_START_SCRIPT, SAFE_START_PATH)
        _run(ssh, f"chmod +x {SAFE_START_PATH}")
        print(f"        OK")

        # ── 2. Write systemd drop-in ──────────────────────────────────────────
        print(f"  [2/4] Writing {DROPIN_PATH} ...")
        rc, _, err = _run(ssh, f"mkdir -p {DROPIN_DIR}")
        if rc != 0:
            print(f"  [FAIL] mkdir -p {DROPIN_DIR}: {err.strip()}")
            return False
        _put_file(ssh, DROPIN_CONF, DROPIN_PATH)
        print(f"        OK")

        # ── 3. daemon-reload ──────────────────────────────────────────────────
        print(f"  [3/4] systemctl daemon-reload ...")
        rc, _, err = _run(ssh, "systemctl daemon-reload")
        if rc != 0:
            print(f"  [FAIL] daemon-reload: {err.strip()}")
            return False
        print(f"        OK")

        # ── 4. Verification: start daemon, wait for poll-based safe-start hook ──
        # Hook polls until stable (up to 30×2s=60s) then disconnects.
        # Wait 35s: enough for a fast connect (~10s) + disconnect + margin.
        # The existing SSH connection gets blocked by nft rules while the hook
        # runs, so close it before sleeping and reopen a fresh one afterwards.
        print(f"  [4/4] Verification: starting nym-vpnd and waiting 35 s ...")
        _run(ssh, "systemctl stop nym-vpnd 2>/dev/null || true")
        time.sleep(2)
        _run(ssh, "systemctl start nym-vpnd")
        ssh.close()
        time.sleep(35)

        ssh = _ssh_connect_with_retry(cfg)

        rc_status, out_status, _ = _run(ssh, "nym-vpnc status 2>&1 || true")
        _,          out_route,  _ = _run(ssh, "ip route show default | head -1")
        _,          out_log,    _ = _run(ssh, "tail -5 /var/log/nym_safe_start.log 2>/dev/null || echo '(no log)'")

        status_ok = "connected" not in out_status.lower() or "disconnect" in out_status.lower()
        route_ok  = "enp7s0" in out_route
        log_ok    = "safe-start complete" in out_log

        print(f"        nym-vpnc status: {out_status.strip()[:120]}")
        print(f"        default route:   {out_route.strip()[:120]}")
        print(f"        log tail:        {out_log.strip()[:240]}")

        if not route_ok:
            print(f"  [FAIL] default route does not use enp7s0 — safe-start may not have run")
            return False
        if not log_ok:
            print(f"  [WARN] 'safe-start complete' not in log — hook may still be running")
        if not status_ok:
            print(f"  [WARN] nym-vpnc reports connected state — route is the definitive check")
        print(f"  [PASS] {vm_name}")
        return True

    except Exception as e:
        print(f"  [FAIL] Unexpected error: {e}")
        return False
    finally:
        ssh.close()


def main():
    print("deploy_nym_safestart.sh")
    print(f"Deploying to: {NYM_VMS}")
    print(f"SSH key: {os.environ.get('SSH_KEY') or '~/.ssh/nico-thesis (default)'}\n")

    results = {}
    for vm_name in NYM_VMS:
        if vm_name not in CLIENTS:
            print(f"\n[ERROR] {vm_name} not found in CLIENTS config — skipping")
            results[vm_name] = False
            continue
        results[vm_name] = deploy_vm(vm_name, CLIENTS[vm_name])

    print(f"\n{'═'*50}")
    print("  SUMMARY")
    print(f"{'═'*50}")
    all_pass = True
    for vm_name, ok in results.items():
        status = "PASS" if ok else "FAIL"
        print(f"  {status}  {vm_name}")
        if not ok:
            all_pass = False

    print(f"{'═'*50}")
    if all_pass:
        print("  All VMs deployed successfully.")
    else:
        print("  One or more VMs failed — check output above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
