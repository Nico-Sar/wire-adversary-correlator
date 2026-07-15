# Patch 07 — router SSH health-check + reconnect (ingress/egress)

**Status: prepared (`patches/07_tor_client1_router_ssh_reconnect.patch`), NOT applied.**

## The problem, precisely

`run_dataset()` opens `ingress_ssh` and `egress_ssh` exactly once, at the
top of the function:
```python
ingress_ssh = retry_ssh_connect(INGRESS_ROUTER)
egress_ssh  = retry_ssh_connect(EGRESS_ROUTER)
client_ssh  = retry_ssh_connect(CLIENTS[client_id])
```
`client_ssh` gets checked and reconnected every visit attempt (the
`if not (client_ssh.get_transport() and ...)` block at the top of the
wedge-aware loop). `ingress_ssh`/`egress_ssh` get no equivalent treatment
anywhere — they're passed by value into `run_single_visit` and never
reassigned by the caller.

If a router SSH session drops (confirmed on tor-client1's backfill runs:
captures started failing and never recovered for the rest of that run),
every subsequent `start_remote_capture()` call on that router raises inside
`start_ingress()`/`start_egress()` (caught into
`ingress_err_box`/`egress_err_box` — see the comment there from the
2026-07-04 tshark-leak fix), and `run_single_visit` returns
`"skipped_tshark_failed"` for every visit, forever, for every client
sharing that router — not just tor-client1. Router-side failures are
deliberately *not* routed into client wedge-recovery today (rebooting the
client VM doesn't fix a dead router SSH session), but nothing else fixes it
either — it's a dead end.

## What the patch does

File: `collector/coordinator.py`. One new helper, two call sites:

**`ensure_router_ssh(ssh_client, router_cfg, label)`** (added next to
`retry_ssh_connect`) — mirrors `client_ssh`'s transport-liveness check:
returns the same client if its transport is active, otherwise calls
`retry_ssh_connect` (which already retries 5x/15s internally). Raises after
`_ROUTER_SSH_RECONNECT_MAX_ATTEMPTS` (3) bounded rounds if the router is
still unreachable.

**Call sites**: at the top of both wedge-aware loops in `run_dataset` (the
primary per-URL loop and the backfill loop), right before the existing
`client_ssh` transport check:
```python
try:
    ingress_ssh = ensure_router_ssh(ingress_ssh, INGRESS_ROUTER, "ingress")
    egress_ssh  = ensure_router_ssh(egress_ssh,  EGRESS_ROUTER,  "egress")
except Exception as e:
    # alert once per outage (not once per retry), block, retry
    ...
```
On success (including a transparent reconnect), execution falls through to
the normal visit-attempt logic unchanged. On failure, it fires **one**
alert per outage (tracked via a `router_down_alert_active` flag, same
one-alert-per-breach pattern the existing zero-success/success-rate alerts
already use — not one alert every 60s for a multi-hour outage), sleeps
`_ROUTER_SSH_DOWN_POLL_S` (60s), and retries — without counting against
`WEDGE_MAX_RECOVERY_ATTEMPTS` or the current visit's `visit_attempt`.

## Why it blocks instead of giving up after N attempts, unlike client_ssh

This is a deliberate difference from `client_ssh`'s own treatment, not an
oversight. `client_ssh`'s wedge loop eventually gives up on one *visit* and
moves to the next — reasonable, because a wedged client is a
single-client problem and giving up unblocks progress on everything else.
A dead **router** is shared infrastructure: every client using it is
blocked identically, so "give up on this visit and move to the next" would
just fail the next one too, and the one after that. Blocking with a bounded
backoff and a single loud alert is the more correct behavior here — it
self-heals the moment the router comes back, without burning through
visit_ids for a failure that isn't the client's fault. This is *not* routed
through `recover_wedged_client()` either, for the same reason: that
function's three-tier escalation (nym reconnect → service restart → hcloud
reset) is entirely about recovering a client VM and has no router-relevant
action to take.

**Known limitation, stated plainly**: if a router is down for a very long
time (hours), this design blocks the calling coordinator process
indefinitely rather than exiting or escalating further than the one alert.
That's an intentional tradeoff (no better alternative exists inside this
process — collection literally cannot proceed without the router), but it
does mean a human needs to see the alert and act; there's no auto-escalation
beyond it. Flagged, not solved, here.

## Risk / independence from patch 06

Verified by generating both patches from the same pristine
`collector/coordinator.py` and applying them in sequence to a scratch copy:
they touch disjoint regions (06 touches the wedge-error-marker constant and
`rotate_circuit_nym`; 07 touches the area near `retry_ssh_connect` and the
top of both wedge-aware loops) and apply cleanly together with no conflicts
(`patch` reported clean hunks, only line-number offsets from 06 already
being applied; `py_compile` passed on the combined result). Safe to apply
independently or together.

## Why this is prepare-only, not applied now

Same reasoning as patch 06: `collector/coordinator.py` is open and actively
executing in every live coordinator process right now. This patch's new
code sits on the hot path of every single visit attempt (both loops check
router liveness before every visit), so it needs a deliberate pause + full
restart, not a live edit.

## How to apply, at the next deliberate pause

```bash
patch -p0 < patches/07_tor_client1_router_ssh_reconnect.patch
# (verified: `patch -p0 --dry-run` applies cleanly against the current
# collector/coordinator.py; `git apply` balks at the patch's synthetic
# /tmp target path in its +++ header — use `patch`, not `git apply`, here)
python3 -m py_compile collector/coordinator.py                # already verified locally
rsync -av collector/coordinator.py leroy:/volume1/scratch/r1086364/wire-adversary-correlator/collector/coordinator.py
```
Requires restarting every coordinator process (all 8 clients share the same
two routers) — a partial restart would leave some coordinators on the old
in-memory module with the bug still present for their share of router
traffic.
