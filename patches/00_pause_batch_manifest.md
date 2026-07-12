# Pause-batch manifest (as of 2026-07-12)

Everything below is prepared and NOT applied. Nothing in this document was
run against the live campaign; it's a plan for the next deliberate pause.

## Two NEW findings from today's session (not patches — need eyes, not code)

1. **`nym5-client1` is currently unreachable via SSH**, confirmed from both
   this machine and from leroy directly (`ssh -i nico-thesis
   root@204.168.204.120` times out both ways). Context: it's not mid-visit —
   it cleanly finished its entire round_03 quota (2400/2400 visits, last one
   `success`) at 2026-07-10 22:47 and its coordinator process exited
   normally. It's been idle and *unmonitored* since (no coordinator is
   currently polling it — nothing watches a client that already finished its
   quota), so if it wedged sometime after that, nothing would have caught
   or fixed it. **This needs a manual check (Hetzner console or `hcloud
   server reset nym5-client1`) — not a code fix, and outside anything this
   session was authorized to act on.** Worth doing before round_04 needs
   this client again.
2. **`~/.ssh/config`'s `nym5-client2` alias points to a stale IP**
   (204.168.201.84) that now presents a different host key than before —
   likely a leftover from before the VM's current IP (178.104.191.219, the
   one `config/infrastructure.py` and the live coordinator actually use)
   was assigned. Low-priority hygiene; worth fixing so a future `ssh
   nym5-client2` by alias doesn't hit the wrong host or a scary
   host-key-changed warning.

## The batch

| # | Fix | File(s) | Risk | Independent? |
|---|---|---|---|---|
| 06 | nym5-client2: broaden retry-eligible visit failures (timeouts, `NS_ERROR_CONNECTION_REFUSED`) + post-rotation gateway reachability probe | `collector/coordinator.py` | **Medium-High** — hot path, every visit attempt; broadens retry scope for all modes, not just nym5 (see patch doc) | Yes — verified no line overlap with 07, applies cleanly alone or combined |
| 07 | Router SSH (ingress/egress) health-check + reconnect, mirroring `client_ssh` | `collector/coordinator.py` | Medium — hot path, but only activates on router-transport failure; requires restarting **all 8** coordinators (shared routers) | Yes — same as above |
| 04 | Remove stale `KDE_PER_MODE` dict from leroy's `config/hyperparams.py` (dead code; local copy already clean) | `config/hyperparams.py` | Low — pure dead-code removal, but file is imported live by `coordinator.py` for `VISIT_TIMEOUTS` | Yes — no functional overlap with anything else in this list |
| 05 | Heartbeat deadline-projection check (7th check in `campaign_heartbeat.py`) | `scripts/campaign_heartbeat.py` | Low — additive, cron-driven, independent of the coordinator process tree | Yes |
| 03 | Second-pass KDE duration retune (vpn 6→4s, tor 24→32s, nym2 30→16s) | `config/kde_params.py` | Low, but **needs a pre-step**: re-run `kde_shape_check.py` on a rebuild with the candidate values before locking in — not yet done | Yes — file isn't imported by the live campaign at all |
| 01 | Round-barrier decoupling (two independent full/light-grid loops, removes the backfill subsystem) | `scripts/run_campaign.sh`, `scripts/run_stage.sh` | **High** — restructures control flow across 2 files, changes what "a round" means operationally, removes a subsystem. Design-only, deliberately **not** turned into a mechanical patch (see `01_round_barrier_decoupling.md` for why) | Standalone, but should be built/reviewed as working code, not applied blind |
| — | nym2-client1/nym2-client2 intermittent SSH/WireGuard re-establishment failures after a reset | *(none — not yet prepared)* | — | **Listed in the task brief as "previously-prepared" — it is not.** `memory/project_state.md` records this as a known, still-open issue from the 2026-07-04/05 investigation ("cause not fully identified... treat as a known flaky spot, not yet resolved"), but no patch file exists for it anywhere in the repo. Flagging the discrepancy rather than fabricating one — this needs its own diagnosis session before it can join a pause batch. |

## Independent vs. interacting

- **06 and 07** both edit `collector/coordinator.py` but touch disjoint
  regions (verified: generated both from the same pristine file, applied in
  sequence to a scratch copy, no conflicts, combined result still compiles).
  Safe to apply either alone or together. Because restarting coordinators
  is the actually-disruptive part (not the patching), and 07 requires an
  all-8-clients restart anyway (shared routers), it's more efficient to
  apply both in the same pause and do one restart round, not two.
- **04** touches a different file `coordinator.py` imports (`hyperparams.py`
  → `VISIT_TIMEOUTS`) but the patch itself doesn't touch `VISIT_TIMEOUTS` —
  no functional interaction with 06/07, just bundle-able into the same
  restart window for convenience.
- **03 and 05** touch files nothing in `collector/` imports at all
  (`kde_params.py`, `campaign_heartbeat.py`) — genuinely independent of
  everything else, including each other. 05 doesn't even need a coordinator
  restart (cron picks it up on its next tick); 03 doesn't need one either
  (nothing live reads it) but needs its own pre-verification step first.
- **01** is orthogonal code-wise (different files) but operationally the
  biggest lever: if applied, it changes how `run_campaign.sh` invokes
  coordinators at all, which would happen *around* whatever restart 06/07
  need, not in conflict with it. Given its size and the July 19 deadline,
  recommend treating it as a separate, later effort — not part of this
  batch's restart cycle.

## Time estimate if everything code-ready (04, 05, 06, 07) is applied together

- Stop points: let the in-flight visit on each of the 8 coordinators finish
  naturally, or accept losing at most one in-flight visit per client to a
  hard stop — a few minutes either way.
- Apply patches (`patch -p0` ×2 for 06/07 — verified `git apply` rejects
  their synthetic /tmp header path, use `patch` — plus `rsync` ×3 for
  04/05/03) + local `py_compile` check:
  ~5 minutes.
- Restart all 8 coordinators, confirm each passes `check_infrastructure`
  preflight and resumes correctly from its existing `_visits.jsonl`
  (all coordinators already resume safely by design — confirmed in
  `memory/project_state.md`): ~15-20 minutes including watching the first
  cycle or two.
- Specifically watch nym5-client2 through one full rotation to confirm the
  new retry classification and reachability probe actually fire as
  expected (look for `[rotate-nym] post-rotation gateway probe` log lines
  and confirm a `Timeout...exceeded` visit gets requeued instead of
  terminal): ~5-10 minutes.
- **Total: roughly 30-45 minutes**, most of it watching restarts settle
  rather than active work.

3 (KDE retune) adds a separate, deferrable pre-step (`kde_shape_check.py`
re-run) of ~10-15 minutes that doesn't block the rest of the batch — it can
be done before or after, independently.

1 (round-barrier decoupling) is explicitly **not** part of this estimate —
building and testing it as working code is a multi-hour effort of its own,
separate from this batch.

## Minimal subset that specifically unblocks the July 19 deadline

**Patch 06 alone** (nym5-client2 retry classification), applied to at least
the two nym5 coordinators (nym5-client1, nym5-client2) and restarted. It's
the direct fix for the diagnosed blocker: nym5-client2 currently loses
~54% of its failed visits permanently (45% timeouts + 9%
`NS_ERROR_CONNECTION_REFUSED`) with zero retry, which is what's making it
the slow link inside the nym5 mode and — per patch 01's diagnosis — the
reason vpn/tor/nym2 are structurally stuck behind the shared round barrier
waiting on nym5 to finish each round. Fewer permanently-lost nym5-client2
visits means fewer wasted visit slots and faster real per-round progress,
without needing patch 01's larger restructuring at all.

Everything else in this batch (07, 04, 05, 03) is good hygiene / real
fixes but not what's currently on the critical path to July 19 — 07 in
particular fixes tor-client1's *backfill*-only capture gap
(`memory/project_state.md` confirms tor-client1's primary data is already
clean), which is lower urgency than the nym5 round-barrier blocker.
