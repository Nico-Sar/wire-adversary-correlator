# Patch 01 — decoupling the round-barrier (Tier 2 proposal + Tier 3 status)

**Status: DESIGN ONLY. No `.patch` file — see "Why no mechanical patch" below.**

## The problem, precisely

`scripts/run_campaign.sh` drives one loop, one round counter, shared by both
URL grids (`full` for vpn/tor, `light` for nym5/nym2). Each iteration calls
`run_stage.sh` once with that round's full+tor+light stage files, launches
whichever clients have an active stage, and then does:

```bash
for client_id in "${!PIDS[@]}"; do
    wait "${PIDS[$client_id]}"     # <- blocks on EVERY launched client
done
```

(`scripts/run_stage.sh`, around line 300). This `wait` loop is *unconditional*
— it blocks on nym5-client2 exactly the same as it blocks on vpn-client1,
even though vpn-client1 finished its actual quota hours earlier and is only
still running because of the backfill loop (`BACKFILL_STOP` file, watched by
a background monitor that only fires once *both* nym5 PIDs exit — see
`run_stage.sh` "3c. Backfill stop-file monitor"). Only after every PID in
that round exits does `audit_stage.sh` run and `.audit_passed` get written,
which is the only thing that lets `run_campaign.sh`'s loop advance to round
N+1 for *any* mode. So vpn/tor/nym2 are structurally unable to reach round 8
(val) or round 10 (test) before nym5 finishes rounds 3 through 7, no matter
how fast they personally run.

## Proposed decoupling (what was asked for)

Split the single shared loop into **two independent loops**, one per grid,
run concurrently:

- **Full-grid loop** (vpn/tor): iterates rounds 1–11 of the full/tor stage
  files only, launching only vpn/tor clients each round, auditing only
  vpn/tor data, advancing the moment vpn+tor finish *their own* round —
  never waiting on nym5/nym2.
- **Light-grid loop** (nym5/nym2): iterates rounds 1–7 of the light stage
  files only, independent of the full-grid loop's progress.

This requires touching three files:
1. **`run_campaign.sh`**: restructure into two `for` loops, each backgrounded
   (`&`), with a single `wait` at the very end for both. Each loop calls
   `run_stage.sh` with `NONE` for the other grid's argument (e.g. the
   full-grid loop always passes `light_urls=NONE`).
2. **`run_stage.sh`**: since a stage call is now always single-grid, the
   entire backfill mechanism (§"3b/3c", the `BACKFILL_STOP` file, the
   monitor subshell) becomes unnecessary and can be deleted — it existed
   *specifically* to keep fast modes busy while trapped waiting on nym5
   within one round; if they're no longer trapped, there's nothing to fill
   time with. This is a net simplification, not just an addition.
3. **`audit_stage.sh`**: checked its actual behavior (read-only, no changes
   made) — every one of its per-mode checks already does
   `[[ -f "$STAGE_DIR/${mode}_visits.jsonl" ]] || continue` before looking at
   that mode, for all four modes, consistently throughout the script. It
   already tolerates a round directory where some modes have no file at all
   (this happens today too, whenever a grid's stage argument is `NONE`) — so
   this file likely needs **no changes** for the decoupling, which shrinks
   the real scope of this patch to just `run_campaign.sh` and `run_stage.sh`.

Round directories (`data/campaign/round_NN/`) do **not** need to be renamed
or restructured — a round directory already only contains whichever modes
were actually launched into it (this is true today: a round with no active
light stage already has no `nym5_visits.jsonl`). The two loops will simply
be at different round numbers at any given moment (e.g. `round_08/` holding
only vpn/tor data while `round_03/` is still being written by nym5/nym2),
which the existing directory scheme already tolerates.

## A smaller, lower-risk alternative worth considering first

The diagnosis in this same task round (see the nym5-client2 report) found
the mode itself isn't inherently 9x slower — one specific client is. A much
smaller intervention that doesn't touch the round/orchestration architecture
at all: once nym5-client1 exhausts its own `--visits` quota for a round,
redirect its still-idle capacity to work through client2's remaining
backlog for the same round, instead of sitting idle waiting for client2 (the
mode already does something structurally similar for vpn/tor's *backfill*,
just never for rebalancing within one mode's own two clients). This
wouldn't unblock vpn/tor from the round barrier the way the full decoupling
does, but it directly shrinks the actual bottleneck driving the barrier
(client2's slow solo pace) with a much smaller, more surgical change — worth
weighing against the full decoupling given the time pressure to the July 19
deadline: the smaller fix is faster to build and review, the full decoupling
is the more complete/durable answer.

## Why no mechanical `.patch` file, unlike patches 03/04/05

The other three Tier 3 items are each a small, self-contained, mechanically
generated diff against one file, safe to review in isolation. This one
restructures the control flow across three files, changes what "a round" 
means operationally, and removes an entire subsystem (backfill) — the kind
of change that genuinely benefits from being written and reviewed as working
code with someone watching, not generated as a diff sight-unseen and
reviewed after the fact. Producing a mechanical patch here would create a
false sense that it's as low-risk to apply as the others, which it isn't.
Recommend treating this as a scoped follow-up task at the next deliberate
pause, starting from this document, rather than a patch to `git apply`.
