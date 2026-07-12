# Patch 05 — deadline-projection check in `campaign_heartbeat.py`

**Status: prepared (`patches/05_heartbeat_deadline_projection.patch`), NOT applied.**

## What it adds

A 7th heartbeat check, non-critical (folded into the existing hourly digest
email, same as the frozen-count and router-drop checks — no new email
channel). Each run:

1. Records `(timestamp, per-mode success count)` into a rolling window
   (`state["rate_history"]`, kept to the last `RATE_WINDOW_HOURS=48`).
2. Once at least `MIN_RATE_WINDOW_HOURS=6` of history exists, estimates each
   mode's successes/day from the oldest sample still in the window.
3. Compares that rate against how many more rounds the mode's grid needs
   (full grid — vpn/tor — vs. light grid — nym5/nym2 — counted from the
   actual `data/campaign/_url_slices/{full,light}/stage_*.txt` files, not
   hardcoded, so it stays correct if the URL lists ever change).
4. If the projection falls short of `LICENSE_DEADLINE` (constant, currently
   `2026-07-19` — update if the license is ever extended), appends a
   `noncritical` warning with the arithmetic spelled out.

Syntax-checked and smoke-tested against synthetic state/campaign data (not
against leroy — this only touches a scratch copy in `/tmp`, never the real
`data/campaign/`).

## Important limitation — read before applying

This projection is **only fully trustworthy for nym5**. vpn/tor/nym2 have a
backfill loop (`run_stage.sh`'s `--backfill-urls`) that keeps their success
counts climbing even while the round itself is stalled waiting on nym5 (the
shared round-barrier the 2026-07-12 audit identified as the actual
bottleneck) — so their raw rate can look fast while real round progress is
zero. The patch emits an inline caveat on every non-nym5 warning saying
exactly this, but it's worth restating here: **if you only read one mode's
projection, read nym5's** — it has no backfill loop, so its rate directly
reflects when the shared round actually closes.

A more precise version would need to distinguish primary-quota visits from
backfill visits within each mode's `_visits.jsonl` (they're not currently
tagged separately) and project off primary-only progress for the fast
modes. Flagged as a possible follow-up, not implemented here — this version
is a deliberately coarse early-warning signal, not a scheduler.

## Why this is Tier 3 and not applied now

`campaign_heartbeat.py` runs via cron, independent of the live
`run_campaign.sh`/`run_stage.sh` process tree, and doesn't write anything
the campaign reads back — so applying it wouldn't touch the live campaign
directly. It's queued here anyway because updating a script that's actively
being invoked by cron (potentially mid-run) fits the same "don't edit a live
process's script without a deliberate pause" caution as the others, and
because the limitation above deserves a conscious go/no-go rather than a
silent drop-in.

## How to apply, at the next deliberate pause

```bash
# review the diff, then:
patch -p0 < patches/05_heartbeat_deadline_projection.patch  # (or git apply)
rsync -av scripts/campaign_heartbeat.py leroy:/volume1/scratch/r1086364/wire-adversary-correlator/scripts/campaign_heartbeat.py
# cron picks it up on its next 30-min tick automatically — no restart needed.
```
