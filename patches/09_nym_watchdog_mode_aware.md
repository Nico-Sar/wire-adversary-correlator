# Patch 09 — mode-aware nym_watchdog.sh + lock-protected coordinator recovery

**Status: prepared, NOT applied, NOT deployed to any VM.**
**Root-caused via read-only investigation 2026-07-12 while the campaign was
deliberately stopped for patches 06/07/08 (see that investigation's report
in this same conversation) — this patch is the fix for it.**

## Recap of the bug

`nym-vpnc`'s tunnel mode (mix vs. WireGuard) is controlled by a persisted
`two-hop` setting that's never explicitly asserted anywhere in this repo —
confirmed by grepping the whole codebase for `two-hop`/`two_hop`: zero
matches, in either `nym_watchdog.sh` or `coordinator.py`. Both simply trust
whatever the current setting already is. `nym_watchdog.sh` is also deployed
byte-for-byte identical to all 4 nym VMs with no way to know which mode any
given VM should be in. When nym5-client2's persisted setting drifted to
WireGuard (cause still unconfirmed), the watchdog kept faithfully
reconnecting it into the wrong mode every ~30-90s, forever — nothing in the
watchdog's own status check ever flagged "connected but in the wrong mode"
as unhealthy. Separately, the watchdog's collection lock
(`/tmp/nym_collection_active`) — which exists specifically so the watchdog
backs off during a visit — was found to be absent for the entire duration
of `coordinator.py`'s own wedge-recovery (`recover_wedged_client`, the
function patch 06's broadened retries requeue through), because the lock is
released *before* the exception that triggers recovery is even raised. Two
independent processes were reconnecting the same tunnel concurrently.

## What changed

### `scripts/nym_watchdog.sh`
- Reads `/etc/nym-watchdog-mode` once at startup (`mix` or `wg`). Missing or
  unrecognized → `INTENDED_MODE=""` and the script behaves **exactly** as
  before the patch (safe default for any VM not yet redeployed).
- `recover()`: for `INTENDED_MODE == "mix"`, asserts
  `nym-vpnc tunnel set --two-hop off` before the final `connect --wait`,
  **verified directly via `nym-vpnc tunnel get`** (confirmed live against
  nym5-client1: reports `Two-hop: off/on` independent of connection state —
  a stronger check than trusting `tunnel set`'s own exit code, and it
  catches a failed assertion *before* wasting a connect attempt on it, not
  just after). Retried up to 3x. Also verifies the post-connect status
  shows `mix` as a second-layer check and logs a loud warning if it
  doesn't. The socks5 disable/enable block is now skipped only for
  confirmed `wg` VMs (it still runs unconditionally for `mix` and for
  unknown/unmigrated VMs, matching old behavior exactly in the unknown
  case).
- Main loop: "connected but in the VM's wrong mode" is now treated the same
  as "not connected" — triggers `recover()` instead of being accepted as
  healthy forever. This is what actually closes the bug long-term: even if
  something else causes a future drift, the VM self-corrects on the next
  30s cycle instead of getting silently stuck again.

### `scripts/deploy_nym_watchdog.sh`
- Fixed nym5-client2's stale IP (`204.168.201.84` → `178.104.191.219`,
  matching `config/infrastructure.py` — the stale one now belongs to a
  different host with a different SSH key, confirmed live). Re-running the
  old script would have deployed to the wrong machine and silently done
  nothing for the real nym5-client2.
- `NYM_VMS` entries are now `"IP:MODE"` pairs; each VM's mode gets written
  to `/etc/nym-watchdog-mode` immediately before the service is
  (re)started, so mode can't drift out of sync with the IP it's declared
  next to.

### `collector/coordinator.py`
- `recover_wedged_client()` is now a thin wrapper: it acquires
  `/tmp/nym_collection_active` (nym5/nym2 only) via a new
  `_set_nym_collection_lock()` helper — its own short-lived SSH connection,
  independent of whatever connection state the actual recovery logic
  (renamed to `_recover_wedged_client_impl()`, otherwise unchanged) is
  juggling across its 3 tiers — and releases it in a `finally`, guaranteed
  regardless of which of the impl's several return points fires.
- `run_single_visit()`: the lock acquire (for nym5/nym2) now happens
  *before* `maybe_rotate_circuit()` instead of after — closes the same
  race for scheduled circuit rotations, not just wedge-recovery. One-line
  reorder, same touch command, same mode gate.

## Verified

- `python3 -m py_compile` on the modified `coordinator.py`: clean.
- `bash -n` on both modified shell scripts: clean.
- The combined patch applies cleanly (`patch -p0`) against fresh copies of
  all three original files and reproduces the intended result byte-for-byte
  (`diff` confirmed empty).
- **CLI syntax independently confirmed live** against nym5-client1
  (read-only — `--help` output and one `tunnel get` status read, no state
  change): `nym-vpnc tunnel --help` lists `set`/`get` subcommands exactly
  as expected; `nym-vpnc tunnel set --help` confirms `--two-hop <on|off>`
  is a real flag; `nym-vpnc tunnel get` on nym5-client1 (currently healthy,
  correctly in mix mode) printed `Two-hop: off`, confirming the
  mix-mode↔two-hop-off correlation and giving a direct, connection-state-
  independent way to verify the assertion took effect (used in the patch
  instead of only inferring mode from post-connect `status` text).

## Risk

This is the gnarly one, as flagged in the investigation. Specifically:

1. **Fleet-wide blast radius.** `nym_watchdog.sh` runs identically on all 4
   nym VMs. The safe-default rule (missing/bad mode file → old behavior)
   protects against a *partial* rollout, but a *bug in the new logic
   itself* would hit all 4 VMs simultaneously once redeployed, both modes.
   CLI syntax is now confirmed (see Verified above), which removes one
   source of that risk, but the *behavioral* interaction with a live
   gateway (does `tunnel set` while disconnected always take cleanly, are
   3 retries enough under real network conditions) is still only verified
   by reading `--help` and one `tunnel get`, not by actually exercising the
   recovery path end-to-end.
2. **New per-VM config file dependency** (`/etc/nym-watchdog-mode`).
   Low risk in isolation (the safe-default rule means a missing file just
   reverts to old behavior), but it's a new piece of state to keep
   consistent, on top of the IP lists already duplicated across
   `deploy_nym_watchdog.sh` / `update_nym_post_connect.sh` — this patch
   fixes the stale IP in the former only; **`update_nym_post_connect.sh`
   still has the same stale nym5-client2 IP and was not touched by this
   patch** (out of scope — it doesn't relate to the watchdog bug — but
   worth fixing separately since it's the same class of staleness).
3. **`recover_wedged_client`'s lock is best-effort, not a hard guarantee.**
   `_set_nym_collection_lock()` never retries and never raises — if the SSH
   connection for the lock touch itself fails (e.g., the VM is genuinely
   unreachable, which is one of the scenarios this function exists to
   handle), the window stays unprotected exactly as it was before this
   patch, for that specific case. This narrows the race, it doesn't close
   it in every scenario.

## Outstanding before deploying

All static verification (syntax, patch-apply correctness, CLI syntax) is
done — see Verified above. What's **not** yet done, because it requires
actually touching the live watchdog service, which this investigation was
scoped not to do without explicit sign-off:

1. **An actual end-to-end test of `recover()`'s new mix-mode branch** —
   i.e., deliberately triggering a recovery on one VM and watching
   `tunnel get`/`status` confirm it lands in `mix` mode, not just reading
   `--help` text. Recommend nym5-client1 specifically (currently the known-
   healthy one) — low stakes to confirm the mode-aware path doesn't
   regress an already-correct VM, before trusting it on nym5-client2 (which
   is the one actually broken right now) or either nym2 VM.
2. **A live sanity check that `_set_nym_collection_lock()` doesn't slow
   down or interfere with normal (non-wedged) recovery** — it adds one
   extra SSH round-trip at the start and end of every `recover_wedged_client`
   call, which should be negligible next to the existing Tier 1a/1b/2
   timings, but hasn't been measured against a real recovery.

## Local repo note

The local working tree's `collector/coordinator.py` does **not** have
patches 06/07/08 applied — those were applied directly on leroy earlier in
this session and never synced back locally. This patch's `coordinator.py`
hunks were generated against leroy's actual post-06/07/08 file (confirmed
via checksum) and verified to apply cleanly against that state — but
running `patch -p0 < patches/09_nym_watchdog_mode_aware.patch` against the
*local* repo copy of `coordinator.py` will fail, because the local file is
missing 06/07/08's changes as its base. Apply this on leroy, or sync
leroy's current `coordinator.py` back to local first.

## How to apply, once the outstanding items above are cleared

```bash
patch -p0 < patches/09_nym_watchdog_mode_aware.patch   # touches all 3 files in one diff
python3 -m py_compile collector/coordinator.py
bash -n scripts/nym_watchdog.sh
bash -n scripts/deploy_nym_watchdog.sh
rsync -av collector/coordinator.py scripts/nym_watchdog.sh scripts/deploy_nym_watchdog.sh \
    leroy:/volume1/scratch/r1086364/wire-adversary-correlator/
```
Deploying the watchdog fix itself requires actually running
`deploy_nym_watchdog.sh` against the live VMs — a separate, explicit step
from applying the patch, since it touches a service that's running on all 4
nym VMs *right now*, independent of whether the coordinator/campaign is
stopped.
