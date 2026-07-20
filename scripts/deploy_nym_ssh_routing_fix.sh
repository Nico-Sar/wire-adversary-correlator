#!/usr/bin/env python3
"""
scripts/deploy_nym_ssh_routing_fix.sh
======================================
Deploys the SSH-survival policy route to all 4 Nym VMs.

ROOT CAUSE ADDRESSED (diagnosed + fixed live, 2026-07-04):
  Each Nym VM has a netplan-declared "from <public_ip> lookup 100" rule
  routing SSH-relevant traffic via table 100 (eth0's public gateway),
  installed at priority 100. nym-vpnc installs its OWN ip rules (observed
  at priority 42-43) when connecting/reconnecting/rotating — a LOWER
  priority number wins, so nym's rules get evaluated before netplan's ever
  does. Once nym is active, SSH traffic can be captured by nym's rule and
  routed into its tunnel instead of out the public interface, killing SSH.
  (An earlier fix attempt used an iptables fwmark to steer SSH packets into
  table 100 — that mark, 0x14d, turned out to collide with a mark
  nym-vpnd's own nftables ruleset uses internally, making it actively
  counter-productive. This script's rule is plain source-IP based instead,
  with no fwmark involved at all.)

UPDATE (2026-07-15, diagnosed + fixed live on nym2-client2 after it went
fully SSH/ICMP-unreachable post-reboot): priority 5 stopped being low enough.
nym-vpnd now also installs a priority-4 catch-all kill-switch rule —
"not fwmark 0x14d -> lookup 333" (routes anything NOT carrying nym's own
tunnel-infra fwmark into the tunnel table) — which is evaluated before
priority 5 ever is. The mangle-mark exemption below (step 5) is supposed to
route around this by marking SSH's own traffic with that same 0x14d, but it
depends on those mangle rules surviving reboots/nftables changes intact;
confirmed live that when they don't (e.g. after an `nft flush ruleset`,
since Ubuntu's iptables is nftables-backed under the hood — an iptables rule
IS an nftables rule here), SSH breaks completely (not just "captured by the
tunnel like before" but zero response to ICMP or TCP at all) even though the
priority-5 rule is present and correct. Moved to **priority 2** — below
nym-vpnd's priority-4 rule too — so the fix wins on pure rule ordering
regardless of fwmark/mangle state. The mangle-mark step is kept as
defense-in-depth (harmless, never even consulted once priority 2 already
resolved the route) but is no longer load-bearing on its own.

SOLUTION:
  1. /usr/local/bin/nym-ssh-routing-fix.sh — idempotent, adds a
     "from <public_ip> lookup 100" rule at priority 2 (below anything nym
     has been observed to install, including its priority-4 kill-switch
     rule — see UPDATE above), trusting netplan's own table 100 route
     as the source of truth rather than re-deriving the gateway (an earlier
     version derived it from eth0's own main-table default route via DHCP,
     which is racy at boot — confirmed live on nym2-client1: that route can
     legitimately not exist yet even though table 100 is already correct).
  2. systemd oneshot unit (nym-ssh-routing-fix.service, enabled at boot)
     applies it on every boot, independent of nym-vpnd's own lifecycle —
     this is what makes it survive a VM reset, unlike the ad-hoc rule that
     existed before this fix (added by hand at some point, never
     reapplied by anything, gone on every reboot).
  3. nym-post-connect.sh (see scripts/update_nym_post_connect.sh) calls it
     again after every nym-vpnc connect/reconnect/rotate, and
     collector/coordinator.py's wedge-recovery Tier 1a chains it too — so
     it's reasserted after every path that can plausibly disturb routing,
     not just at boot.

Also installs the iptables mangle rules that mark SSH traffic (port 22,
both directions) with fwmark 0x14d — the SAME mark nym-vpnd's own nftables
mangle chain uses for gateway-bound traffic. This is NOT optional: once the
tunnel is up, nym-vpnd installs an ip rule "not fwmark 0x14d -> lookup 333"
that routes any UNMARKED traffic (including SSH) into the tunnel table,
breaking SSH. Marking our own SSH traffic with the same 0x14d exempts it
from that catch-all so it falls through to this script's own "from
<public_ip> lookup 100" rule instead. Confirmed live (2026-07-06): without
these mangle rules, SSH reliably breaks within seconds of any connect
attempt regardless of ip-rule priority (0, 1, or 5 all fail the same way);
with them, SSH survives cleanly through a real connect on all modes tested
(nym2 WireGuard, nym5 mixnet). See docs/infrastructure_routing.md for the
full verification history — that document (from an earlier session) already
described this exact mechanism as the verified fix; it just hadn't been
carried over into this deploy script or applied to the 3 rebuilt VMs.

Usage:
    python3 scripts/deploy_nym_ssh_routing_fix.sh
    SSH_KEY=/path/to/key python3 scripts/deploy_nym_ssh_routing_fix.sh
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import paramiko
from config.infrastructure import CLIENTS
from collector.coordinator import _agent_key_matching_pubfile

NYM_VMS = ["nym5-client1", "nym5-client2", "nym5-client3", "nym5-client4", "nym5-client5", "nym5-client6"]

ROUTING_FIX_SCRIPT = """\
#!/bin/bash
# nym-ssh-routing-fix.sh
# Idempotent SSH-survival policy route for Nym client VMs.
#
# netplan (systemd-networkd backend) already declares, per-VM, a static
# "from <public_ip> lookup 100" rule + "default via <public_gw> table 100"
# route for eth0 — this is reliable at boot (static config, not dependent on
# DHCP timing for eth0's own main-table default route, which can legitimately
# be absent/delayed). The gap is priority: netplan's rule installs at
# priority 100, and nym-vpnc has been observed installing its OWN rules at
# priority 42-43 (lower number = evaluated first) — so once nym is active,
# ITS rule wins over netplan's before netplan's ever gets a chance. This
# script's only job is closing that gap: add the SAME routing (same table,
# same source IP) at a priority low enough that nothing nym does can get
# ahead of it, and keep reasserting it after every nym-vpnc connect/
# reconnect/rotate (nym-post-connect.sh) in case nym ever touches rules
# beyond its own.
#
# Do NOT try to (re)detect the table-100 gateway dynamically here (an
# earlier version read `ip route show default dev eth0`, which is racy at
# boot — eth0's own DHCP-assigned default route can genuinely not exist yet,
# or ever, independent of table 100 being fine) — confirmed live
# (2026-07-04, nym2-client1): this exact detection failed at boot before
# eth0's DHCP finished, even though netplan's table 100 was already correct.
# Trust netplan's table 100 as the source of truth; only add the rule.

IFACE=eth0
TABLE=100
PRIORITY=2

# eth0's address itself is assigned immediately at boot (not racy) — it's
# only eth0's DEFAULT ROUTE via DHCP that can be delayed/absent, which is
# exactly why table 100's gateway is no longer derived from that (see above).
MYIP=$(ip -4 addr show "$IFACE" | grep -oP 'inet \\K[0-9.]+' | head -1)
if [[ -z "$MYIP" ]]; then
    echo "nym-ssh-routing-fix: could not determine $IFACE's IP — aborting" >&2
    exit 1
fi

if ! ip route show table "$TABLE" | grep -q '^default'; then
    echo "nym-ssh-routing-fix: table $TABLE has no default route (netplan not applied?) — aborting" >&2
    exit 1
fi

# Drop any stale rules from earlier runs/versions (any priority except the
# canonical one, this VM's own IP as source or destination, any table)
# before adding the one canonical rule — keeps re-runs idempotent even
# across script version changes.
while read -r pref _; do
    pref="${pref%:}"
    [[ "$pref" == "$PRIORITY" ]] && continue
    ip rule del pref "$pref" 2>/dev/null || true
done < <(ip rule show | grep -E "(from|to) $MYIP lookup")

ip rule show | grep -q "^${PRIORITY}:.*from $MYIP lookup $TABLE" \\
    || ip rule add from "$MYIP" lookup "$TABLE" priority "$PRIORITY"

echo "nym-ssh-routing-fix: from $MYIP -> table $TABLE at priority $PRIORITY"
"""

ROUTING_FIX_SERVICE = """\
[Unit]
Description=SSH-survival policy route (survives nym-vpnc circuit changes)
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=10

[Service]
Type=oneshot
ExecStart=/usr/local/bin/nym-ssh-routing-fix.sh
RemainAfterExit=yes
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
"""

ROUTING_FIX_PATH = "/usr/local/bin/nym-ssh-routing-fix.sh"
SERVICE_PATH      = "/etc/systemd/system/nym-ssh-routing-fix.service"


def _ssh_connect_with_retry(host_cfg: dict, max_wait: int = 90, interval: int = 10) -> paramiko.SSHClient:
    # pkey pinned via _agent_key_matching_pubfile (see coordinator.py) --
    # avoids paramiko's blind agent-key enumeration crashing on leroy's
    # unrelated ED25519-CERT identity (AttributeError: public_blob).
    key_path = os.environ.get("SSH_KEY") or host_cfg["key_path"]
    key_filename = str(Path(key_path).expanduser())
    pkey = _agent_key_matching_pubfile(Path(key_filename))
    deadline = time.time() + max_wait
    last_err = None
    while time.time() < deadline:
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                hostname=host_cfg["host"],
                username=host_cfg["user"],
                pkey=pkey,
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
        print(f"  [1/5] Writing {ROUTING_FIX_PATH} ...")
        _put_file(ssh, ROUTING_FIX_SCRIPT, ROUTING_FIX_PATH)
        _run(ssh, f"chmod +x {ROUTING_FIX_PATH}")
        rc, _, err = _run(ssh, f"bash -n {ROUTING_FIX_PATH}")
        if rc != 0:
            print(f"  [FAIL] syntax check failed: {err.strip()}")
            return False
        print(f"        OK")

        print(f"  [2/5] Writing {SERVICE_PATH} ...")
        _put_file(ssh, ROUTING_FIX_SERVICE, SERVICE_PATH)
        print(f"        OK")

        print(f"  [3/5] systemctl daemon-reload, enable, restart ...")
        _run(ssh, "systemctl daemon-reload")
        _run(ssh, "systemctl reset-failed nym-ssh-routing-fix.service")
        _run(ssh, "systemctl enable nym-ssh-routing-fix.service")
        rc, _, err = _run(ssh, "systemctl restart nym-ssh-routing-fix.service")
        if rc != 0:
            print(f"  [FAIL] service failed to start: {err.strip()}")
            return False
        print(f"        OK")

        print(f"  [4/6] Cleaning up old nym-routing-fix.sh remnant ...")
        _run(ssh, "rm -f /usr/local/bin/nym-routing-fix.sh")
        print(f"        OK")

        print(f"  [5/6] Installing fwmark-0x14d mangle rules for SSH (port 22, both directions) ...")
        # Idempotent: only add if not already present.
        rc, out, _ = _run(ssh, "iptables -t mangle -L PREROUTING -n | grep -c '0x14d' || true")
        if out.strip() == "0" or not out.strip():
            _run(ssh, "iptables -t mangle -A PREROUTING -p tcp --dport 22 -j MARK --set-mark 0x14d")
            _run(ssh, "iptables -t mangle -A PREROUTING -p tcp --sport 22 -j MARK --set-mark 0x14d")
            _run(ssh, "iptables -t mangle -A OUTPUT -p tcp --sport 22 -j MARK --set-mark 0x14d")
            _run(ssh, "iptables -t mangle -A OUTPUT -p tcp --dport 22 -j MARK --set-mark 0x14d")
        _run(ssh, "DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent")
        rc, _, err = _run(ssh, "netfilter-persistent save")
        if rc != 0:
            print(f"  [FAIL] netfilter-persistent save: {err.strip()}")
            return False
        _, out_mangle, _ = _run(ssh, "iptables -t mangle -L -n")
        mangle_ok = out_mangle.count("0x14d") >= 4
        if not mangle_ok:
            print(f"  [FAIL] expected 4 mangle rules with 0x14d, found {out_mangle.count('0x14d')}")
            return False
        print(f"        OK")

        print(f"  [6/6] Verification: ip rule show ...")
        _, out_rule, _ = _run(ssh, "ip rule show")
        print(f"        {out_rule.strip().splitlines()}")
        rule_ok = any(line.strip().startswith("2:") and "lookup 100" in line for line in out_rule.splitlines())
        if not rule_ok:
            print(f"  [FAIL] priority-2 rule not found in ip rule show")
            return False

        print(f"  [PASS] {vm_name}")
        return True

    except Exception as e:
        print(f"  [FAIL] Unexpected error: {e}")
        return False
    finally:
        ssh.close()


def main():
    print("deploy_nym_ssh_routing_fix.sh")
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
