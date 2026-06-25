# Routing Architecture: Forcing Nym Traffic Through the Wire Adversary Capture Point

> **Status as of this writing**: all four nym clients
> (`nym2-client1`, `nym2-client2`, `nym5-client1`, `nym5-client2`) are
> migrated, reboot-persistence-verified, and pass a 2-3 visit wire gate each.
> Ingress BPF filters are now per-client scoped (§8) to prevent
> cross-contamination between same-mode clients, and a wedge detection/
> recovery layer (§9) requeues visits affected by the recurring
> `nym-vpnd`-correlated lockups instead of losing or false-passing them.
> Earlier drafts of this document described a netplan-based, all-4-VMs
> deployment as already in place before any of it was verified; that
> speculation has since been replaced by what was actually verified on the
> wire, client by client.

## 1. The Problem

Both `nym2-client1` (`204.168.181.115`, private IP `10.0.0.4`) and
`nym5-client1` (`204.168.204.120`, private IP `10.0.0.9`) have two interfaces:

- **`eth0`** — public Hetzner IP, its own DHCP default gateway (`172.31.1.1`)
- **`enp7s0`** — private IP on the `thesis-client-net` Hetzner Cloud Network
  (`10.0.0.0/16`), gateway `10.0.0.1`. That network has explicit SDN-level
  routes (`hcloud network describe thesis-client-net`) sending `10.1.0.0/16`
  and `0.0.0.0/0` onward via `10.0.0.2` — the ingress router.

By default the VM's main routing table prefers `eth0`. Confirmed live via
`tcpdump -ni any 'not port 22'` on each client while connected: nym2's outer
WireGuard UDP and nym5's outer Sphinx-transport TCP both left directly on
`eth0` to the gateway's public IP, never touching the ingress router:

```
BEFORE FIX
══════════
  [nym2/nym5 client]
       │ eth0 (public IP, default route)
       ▼
  [Internet] ──────────────────────► [Nym entry gateway / mix node]

  [ingress router] (enp7s0 = 10.0.0.2)   ← sees nothing
```

## 2. The Fix

A route change on each migrated client, mirroring the pattern already
working for `tor-client1` (confirmed by reading `tor-client1`'s live routing
table before touching anything):

```bash
ip route replace default via 10.0.0.1 dev enp7s0 proto static onlink
```

### Persistence: netplan (discovered already deployed, then verified)

A netplan config implementing this exact split — `/etc/netplan/99-thesis-routing.yaml`
— was found already present on both `nym2-client1` and `nym5-client1` (table
100 routing-policy for the public IP + `enp7s0` main default route). It is
**not something this investigation authored**; it was deployed independently,
predating the live verification work. Verified for real via two separate
`hcloud server reboot` cycles on `nym2-client1`: after each reboot, with
**zero manual intervention**, the main route was already `enp7s0` and table
100 was already `eth0`. `nym5-client1` was not reboot-tested directly, but
has the byte-identical netplan file.

**`nym2-client2` and `nym5-client2` have not been checked for this netplan
file at all** — do not assume it's there.

### Why this doesn't break SSH

Both VMs have a **pre-existing**, independent SSH-safety mechanism — not
something added for this fix, just verified before relying on it:

| Traffic | Mechanism | Routes via |
|---|---|---|
| SSH (port 22, marked by existing `iptables -t mangle` rules with `fwmark 0x14d`) | `ip rule`: `from <public-ip> lookup 100` (priority 100) | table 100 → `default via 172.31.1.1 dev eth0` |
| Everything else | `ip rule`: `from all lookup main` (priority 32766) | main table → now `enp7s0` |

Verified on both clients via `ip route get <peer> from <public-ip> mark 0x14d`
(simulating a real marked SSH reply packet) resolving via table 100 / `eth0`,
both before and after the main-table change, plus the SSH session itself
surviving the live change in practice on both VMs.

**Caution for future replication**: an `ip route get` *without* the correct
`mark` gives a misleading answer — it falls through to a different rule
(`not fwmark 0x14d → table 333`, used by the Nym tunnel's own internal
routing) and reports a route real SSH traffic never actually takes. Always
include `mark 0x14d` when simulating SSH-path routing decisions on these VMs.

```
AFTER FIX
═════════
  [nym2/nym5 client]
       ├─ SSH reply (fwmark 0x14d) ──► table 100 ──► eth0 ──► internet directly
       │
       └─ everything else (incl. Nym outer flow) ──► main table ──► enp7s0
                                                                       │
                                                                       ▼
                                                           [ingress router] ◄── CAPTURE POINT
                                                           (enp7s0 = 10.0.0.2)
```

## 3. The Real Recurring Hazard: Per-Rotation Route Reset (not the systemd hook)

The first full coordinator test run on nym2 (12 visits, `--rotate-circuits`)
came back with **zero ingress packets on every visit**, despite the route
fix being live and independently verified moments earlier. Root cause: every
Nym circuit rotation runs a script (`_NYM_SCRIPT_PREAMBLE` in
`collector/coordinator.py`) that unconditionally executed:

```bash
ip route replace default via 172.31.1.1 dev eth0
```

This fires on **every single rotation** — far more frequently than the
systemd `ExecStartPost` safe-start hook, which only fires on `nym-vpnd`
start/restart. This preamble line, not the systemd hook, was the dominant
reason the route kept reverting mid-test. The exact same bug reproduced on
`nym5-client1` independently before it was migrated (confirmed live:
rotation reset its main route to `eth0`, traffic observed leaving via `eth0`).

**Fix**: `_NYM_SCRIPT_PREAMBLE` is now `_nym_script_preamble(route_restore)`,
parameterized by a `route_restore` argument (`"eth0"` or `"enp7s0"`).
`maybe_rotate_circuit()` selects `"enp7s0"` only for client IDs in

```python
_NYM_CLIENTS_VIA_INGRESS_ROUTER = {"nym2-client1", "nym5-client1"}
```

`nym2-client2` and `nym5-client2` still get `"eth0"` (original behavior,
unchanged) until each is independently verified and added to that set.

The separate systemd `ExecStartPost` safe-start hook
(`/usr/local/bin/nym-vpnd-safe-start.sh`, deployed by
`scripts/deploy_nym_safestart.sh`) was edited in an earlier session to
restore `via 10.0.0.1 dev enp7s0`. It fires on every `nym-vpnd` start/boot
and was incidentally exercised (and confirmed working) during the
`nym2-client1` reboot tests in §2 — but the per-rotation preamble fix above
is what actually matters for normal `--rotate-circuits` collection runs.

## 4. BPF Filters — Verified, Not Guessed

**`BPF_INGRESS["nym2"] = "(udp port 51822)"`**

Observed across 8 circuit rotations spanning 7 distinct entry gateways
(`194.182.191.207`, `188.244.117.96`, `103.63.30.68`, `45.91.92.139`,
`45.141.119.166`, with repeats) — the destination port was `51822` in every
case. Verified stable for `nym2-client2` too (separate rotations, distinct
gateways). A stable port across rotations got a port-based filter.

**`BPF_INGRESS["nym5"] = "(tcp port 9000)"`**

Observed across 13 circuit rotations total (across `nym5-client1` and
`nym5-client2`, multiple test runs) spanning many distinct entry mix-nodes
— exactly **one** TCP connection per rotation, always port `9000`. Port
`9001` (mentioned as a possibility in the original, pre-verification code
comment) was never observed and is intentionally **not** included.

A host-based filter (`host 10.0.0.9 or host 10.0.0.10`) was tried first and
**rejected**: once a client's *general* traffic also routes via `enp7s0`
(not just the Nym tunnel), a host-scoped filter picks up unrelated
NTP/HTTPS background traffic too, polluting the capture.

These are now **fragments**, not complete filters — see §8.

## 5. Invalid Historical Data

Any `nym5` pcap/dataset collected **before** the `detect_capture_iface`
fix (earlier this project) is almost certainly invalid for a different
reason than the routing issue above: that bug silently captured on `eth0`
for *every* mode due to link-enumeration order, not the configured `enp7s0`.
nym5 captures from that era may have looked non-zero by coincidence (eth0
happened to be both the bug's mistaken target and, at the time, genuinely
where nym5's unmigrated traffic was) — but this was never a verified,
intentional capture path. **Any nym5 dataset built before this session's
fixes should be treated as unverified and re-collected.**

`nym2` ingress data collected before this session's routing fix is
definitionally invalid (confirmed zero packets pre-fix).

## 6. Verification

```bash
# Main table default should now be enp7s0 (for migrated clients only)
ssh root@<client-ip> 'ip route show default'
#   → default via 10.0.0.1 dev enp7s0 proto static onlink

# SSH path must still resolve via table 100 / eth0 — note the explicit mark
ssh root@<client-ip> 'ip route get <peer-ip> from <client-public-ip> mark 0x14d'
#   → ... via 172.31.1.1 dev eth0 table 100 ...

# End-to-end capture check after a coordinator run with --rotate-circuits —
# this is now also enforced automatically by the zero-ingress guard in
# coordinator.py (a visit with an empty ingress pcap is marked ZERO_INGRESS,
# never "success", after one retry).
for f in <output_dir>/<mode>/*ingress*.pcap; do
  tshark -r "$f" 2>/dev/null | wc -l   # must be > 0 for every visit
done
```

## 7. Recovery Runbook

If a migrated client becomes unreachable on its public IP only (SSH/nft
contention during a rotation window):

```bash
until ssh -o ConnectTimeout=8 -o BatchMode=yes root@<client-ip> \
  'systemctl stop nym-vpnd; nft delete table inet nym 2>/dev/null; echo RECOVERED'; do
  echo "...retry"; sleep 5
done
```

If unreachable on **both** public and private IPs (OS networking itself
wedged), recover out-of-band via Hetzner's API, independent of the broken
network path:

```bash
hcloud server reset <client-name>
```

**Observed reliability concern**: across this investigation, `nym2-client1`
wedged on both IPs four times and both `nym5` clients were found
independently wedged (untouched, not caused by this work). The common factor
across every wedge was running the `nym-vpnd`/safe-start stack — not a
specific action taken here, and not generic Hetzner instability (`client1`,
`tor-client1`, and the ingress router were never affected). Root cause is
still undiagnosed — §9 covers surviving it automatically rather than
preventing it.

## 8. Per-Client BPF Scoping (Cross-Contamination Fix)

Port-only ingress filters merge same-mode clients' captures on the shared
ingress interface. Confirmed live: with `BPF_INGRESS["nym2"]` as a bare
port filter, `nym2-client1`'s concurrent port-51822 traffic leaked into a
simultaneously-running `nym2-client2` capture — non-zero, right port,
**wrong client**. The zero-ingress guard cannot catch this; it only checks
for *zero* packets, and a contaminated capture is never zero.

**Fix**: `BPF_INGRESS` dict values are now port/protocol fragments only
(parenthesized, e.g. `"(tcp port 9000)"`), combined per-visit with
`build_ingress_bpf(mode, client_id)` in `config/infrastructure.py`, which
appends `and host <client's enp7s0 private IP>`. `coordinator.py` calls this
instead of indexing `BPF_INGRESS[mode]` directly. Applied to all five
modes (`baseline`, `tor`, `vpn`, `nym5`, `nym2`) even though only nym2/nym5
currently have two same-mode clients that could co-run — `baseline`/`vpn`
have one client each today but share the same ingress interface and BPF
mechanism, so the same contamination would apply if a second client were
ever added.

**Proof**: ran `nym2-client1` and `nym2-client2` concurrently (2 visits
each, genuine temporal overlap confirmed via capture PID timing). Asserted,
per pcap, that no foreign `10.0.0.x` address appears — i.e. `nym2-client1`'s
pcaps contain only `10.0.0.4`, `nym2-client2`'s contain only `10.0.0.6`. All
4 pcaps passed; all 4 were also non-zero (89–135 packets) with the correct
client-scoped flow.

## 9. Wedge Detection and Bounded Recovery

The `nym-vpnd`-correlated wedging in §7 will happen during an unattended
multi-day run. Two tiers of detection/recovery in `coordinator.py`:

- **`check_client_health(client_ssh, mode)`** — soft-wedge signal: SSH
  responsive but `nym-vpnc status` times out / returns an RPC error, SOCKS5
  port 1080 not listening (nym5), or `tun1` missing (nym2).
- **`recover_wedged_client(client_id, client_cfg, mode)`** — bounded
  recovery: tries reconnecting SSH (3 short retries) and restarting
  `nym-vpnd` first ("soft" — cheap, fast); escalates to `hcloud server
  reset` ("hard") only if SSH itself doesn't come back, then waits
  (bounded, up to 240 s) for SSH to return.

Integrated into `run_dataset`'s visit loop as a bounded requeue: each visit
gets a pre-flight health check before `run_single_visit` runs; on failure
(or a mid-visit exception), `recover_wedged_client` is called and, if
successful, the **same `visit_id` is requeued** — not skipped, not logged
as a different visit. After `WEDGE_MAX_RECOVERY_ATTEMPTS` (2) failed
recoveries, the visit is marked `WEDGE_UNRECOVERABLE` and logged to the
JSONL — never silently dropped. Every wedge event (recovered or not) is
recorded and printed in the end-of-run summary:

```
[coordinator] *** wedge events: 1 total (1 recovered, 0 not) on client nym5-client1 ***
    [19:02:04] nym5-client1_v00001: preflight: nym5 SOCKS5 port 1080 not listening → method=soft_restart_nym_vpnd recovered=True
```

**Demonstrated live** (not simulated): induced a real soft wedge
(`systemctl stop nym-vpnd`) on `nym5-client1` mid-run. The next visit's
pre-flight check caught it, soft recovery succeeded, the visit was
requeued, and it went on to produce a genuine non-zero capture
(`10.0.0.9 ↔ 103.63.30.68:9000`, 4975 packets) — not a false "success",
an actual recovered one. Repeated successfully on `nym5-client2` too
(naturally triggered by a fresh-disconnect state, same code path).

**Known false-positive**: the pre-flight check runs before any rotation has
had a chance to establish the tunnel, so a client that's simply mid-startup
(freshly disconnected, about to reconnect) looks identical to a genuine
wedge — SOCKS5 isn't listening yet either way. This is harmless (soft
recovery is just `systemctl restart nym-vpnd`, idempotent) but adds ~20-30s
of overhead at the start of most nym5 runs. Not fixed — flagged as a minor
inefficiency, not a correctness issue.

**Scope note**: only the soft-recovery path was exercised live multiple
times. The hard-recovery path (`hcloud server reset` triggered
*automatically* by `recover_wedged_client`, as opposed to manually as was
done throughout this investigation) is implemented but has not itself been
exercised by an induced hard wedge — inducing a real full network-stack
lockup on demand isn't reliably reproducible, and manually recovering one
mid-test (done 6+ times this investigation) is structurally identical to
what the automated path does, just not yet proven end-to-end as one
unattended sequence.

## 10. Known Gaps / Follow-Up

- All four nym clients (`nym2-client1`, `nym2-client2`, `nym5-client1`,
  `nym5-client2`) are now migrated, reboot-verified, and pass a wire gate.
  `baseline`, `vpn`, `tor` clients were **not** touched — they were never
  reported broken and weren't in scope, but they do share the same
  ingress-capture mechanism and now benefit from per-client BPF scoping
  automatically (§8) since that change applies to all five modes.
- The recurring `nym-vpnd`-correlated wedging (§7) is still unresolved at
  the root-cause level — §9 covers surviving it, not fixing it.
- The automated hard-recovery path (`hcloud server reset` triggered by
  `recover_wedged_client`) has not been exercised by an actually-induced
  hard wedge — see §9's scope note.
- The pre-flight health check's fresh-disconnect false-positive (§9) adds
  minor overhead to most nym5 runs; not fixed.
- Avoid calling `nym-vpnc connect` directly outside of the coordinator's
  rotation script — it bypasses the nft-safety wrapping
  (nohup + `post-connect.sh`) that the production rotate script relies on,
  and was observed to trigger an SSH-blocking nft kill-switch state when
  done as a manual one-off during this investigation.
