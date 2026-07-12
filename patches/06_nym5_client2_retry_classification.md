# Patch 06 — broaden nym5-client2's retry-eligible visit-failure classification

**Status: prepared (`patches/06_nym5_client2_retry_classification.patch`), NOT applied.**

## The problem, precisely

`collector/coordinator.py` only ever requeues a failed visit when
`run_single_visit` raises `SOCKS5WedgeError` — which only happens when
`is_socks5_wedge_error(visit_status)` matches. Today that function checks a
single marker, `"NS_ERROR_PROXY_CONNECTION_REFUSED"`. Everything else
(a plain Playwright `page.goto` timeout, `NS_ERROR_CONNECTION_REFUSED`
without `PROXY_`, any other page-load error string) falls through to
`visit_succeeded = record.visit_status == "success"` → `False`, and the
wedge-aware loop in `run_dataset` just `break`s — the visit is logged with
that failure status and the slot is gone for good, no retry.

A live-campaign audit of nym5-client2 specifically (round_03,
`data/campaign/round_03/nym5_visits.jsonl`) found:
- Plain `Page.goto: Timeout 180000ms exceeded.` accounts for ~45% of
  client2's failed visits.
- `NS_ERROR_CONNECTION_REFUSED` (no `PROXY_`) accounts for ~9%.
- 65.8% of client2's failures land on the **first visit after a circuit
  rotation**, vs. ~34% for later visits on the same circuit.

That last number is the tell: it's the signature of "the freshly-rotated
exit gateway hasn't finished warming up yet," not "the target site is
actually down" — exactly the transient condition the existing wedge-retry
machinery exists to paper over, just not currently wired up to catch it.

Confirmed live while writing this patch: `round_03/nym5_visits.jsonl`
already contains multiple `nym5-client2_v019xx` entries with
`"visit_status": "error: Page.goto: Timeout 180000ms exceeded.\n..."` sitting
as terminal failures, right next to a run of `"success"` entries — i.e. this
is actively happening in the currently-running round, not just historical.

## What the patch does

File: `collector/coordinator.py`. Two independent changes:

**1. `_SOCKS5_WEDGE_ERROR_MARKERS` (around the existing "Mid-visit SOCKS5
wedge classification" section)** — adds two markers:
```python
_SOCKS5_WEDGE_ERROR_MARKERS = (
    "NS_ERROR_PROXY_CONNECTION_REFUSED",
    "NS_ERROR_CONNECTION_REFUSED",   # newly retry-eligible
    "ms exceeded",                   # newly retry-eligible (Playwright timeout)
)
```
Since `is_socks5_wedge_error()` is the sole gate for raising
`SOCKS5WedgeError`, and that exception is what makes `run_dataset`'s
wedge-aware loop requeue the same `visit_id` instead of accepting the
failure as final, this is the whole mechanical change needed to make both
failure classes retryable — it reuses 100% of the existing requeue/recovery
plumbing (`recover_wedged_client`, `WEDGE_MAX_RECOVERY_ATTEMPTS`, alerting).

**2. `rotate_circuit_nym()` — new step "5c", a post-rotation gateway
reachability probe (nym5 only)** — runs immediately after step 5b (SOCKS5
port 1080 confirmed listening), before returning the new circuit info:
- `curl -s -m 8 --socks5 127.0.0.1:1080 -w '%{http_code}' <web-server-url>`
  through the freshly-opened proxy.
- HTTP 200 → done, proceed as before.
- Anything else → re-issue `nym-vpnc socks5 disable` / `socks5 enable
  --exit-random` (not the full disconnect/reconnect sequence — tunnel and
  nftables state are already fine, only the gateway assignment needs to
  change) and probe once more.
- Still not 200 after that → log a warning and continue anyway; the
  broadened retry classification above is the backstop.

This directly targets the root cause (SOCKS5 port being open doesn't mean
the gateway is actually routing yet) rather than only reacting after a full
180s visit timeout has already been spent discovering it.

## Risks — read before applying

1. **`is_socks5_wedge_error()` is evaluated for every mode, not just nym5.**
   `NS_ERROR_CONNECTION_REFUSED` is Firefox/Playwright-specific (curl-driven
   binary downloads never produce it), so that part is low-risk everywhere.
   The `"ms exceeded"` marker generalizes furthest: for tor or vpn, a
   genuinely dead/slow target site (not a wedged gateway) now costs up to
   `WEDGE_MAX_RECOVERY_ATTEMPTS` extra recovery cycles (~40-90s each,
   `recover_wedged_client` always runs its full nym-reconnect/service-restart
   tiers regardless of *why* it was called) before reaching the same
   terminal outcome it hit immediately before this patch. The diagnosis
   here is nym5-client2-specific and doesn't establish whether tor/vpn have
   the same rotation-adjacent failure pattern. **If this proves costly for
   tor/vpn's throughput in practice, scope the two new markers to `mode in
   ("nym5", "nym2")` at the call site** — not done here since the live
   evidence only supports nym5 today, and narrowing preemptively without
   evidence would be guessing.
2. **No new retry-cap constant was introduced, and that's deliberate.**
   `WEDGE_MAX_RECOVERY_ATTEMPTS` (already 2) is a single, shared, per-
   visit-slot ceiling that every wedge cause funnels through via the one
   `visit_attempt` counter in `run_dataset`'s loop. A genuinely dead
   gateway still gets at most 3 total attempts (1 initial + 2 recovery),
   the same bound SOCKS5-refused already had — it just now applies to two
   more failure classes instead of zero.
3. **`recover_wedged_client` doesn't know *why* it was called** — it always
   runs the same three-tier escalation (nym reconnect → service restart →
   hcloud reset) regardless of whether the trigger was a genuinely wedged
   proxy or just a slow site. For the two new marker classes this means a
   requeued visit gets a real (if untargeted) recovery attempt rather than
   an instant re-try — slower per retry, but not incorrect, and consistent
   with how SOCKS5-refused is already handled.

## Why this is prepare-only, not applied now

Both changes are inside `collector/coordinator.py`, which every live
coordinator process currently has open and is actively executing — exactly
the file the blanket "no live edits without a deliberate pause" rule
protects. `rotate_circuit_nym` in particular is on the hot path for every
nym5/nym2 rotation across all four nym coordinators; editing it while any
of them are mid-rotation is not safe.

## How to apply, at the next deliberate pause

```bash
patch -p0 < patches/06_nym5_client2_retry_classification.patch
# (verified: `patch -p0 --dry-run` applies cleanly against the current
# collector/coordinator.py; `git apply` balks at the patch's synthetic
# /tmp target path in its +++ header — use `patch`, not `git apply`, here)
python3 -m py_compile collector/coordinator.py                 # already verified locally
rsync -av collector/coordinator.py leroy:/volume1/scratch/r1086364/wire-adversary-correlator/collector/coordinator.py
```
Requires restarting the affected coordinators (a running process has
already imported the old module — a live `rsync` alone does not hot-swap
it). Restart nym5-client1/nym5-client2 at minimum; tor/vpn/nym2 restarts
are optional given the scoping risk in point 1 above, but should happen
together with patch 07 (both touch `coordinator.py`) rather than as two
separate restarts.
