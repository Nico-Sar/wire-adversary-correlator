# Patch 03 — second-pass KDE duration retune

**Status: prepared (`patches/03_duration_retune.patch`), NOT applied.**

## What changed

| Mode | Current | Proposed | Why |
|---|---|---|---|
| vpn  | 6.0s  | 4.0s  | Audit spot-check (n=20, round_02, ingress only): max=2.87s, p95=2.80s. Still >20% margin over the original n=300 dual-stream study's egress max=3.29s (the authoritative bound — see reasoning below). |
| tor  | 24.0s | 32.0s | Audit spot-check: median=12.31s, but 2/20 (10%) samples ran 37–38s and got truncated. Loosening to 32s cuts that truncation rate without materially reopening the padding problem the original retune fixed (still far short of the true long-tail p95=85.69s from the n=300 study — truncation of genuine outliers remains a deliberate, accepted tradeoff, just a slightly less aggressive one). |
| nym5 | 30.0s | unchanged | Spot-check max=28.11s, no truncation observed — value already fits. |
| nym2 | 30.0s | 16.0s | Audit spot-check max=12.23s, close to the original pilot study's max=9.8s this value was sized against. 16.0s keeps ~30% margin over the larger of the two. |

Full diff: `patches/03_duration_retune.patch` (unified diff against
`config/kde_params.py`, applies cleanly with `git apply` or `patch -p0`).

## Important caveat — read before applying

The new numbers come from a **single n=20, ingress-only spot-check on one
round** (round_02) — much thinner evidence than the original retune, which
sampled n=300 flows per mode, both ingress AND egress, specifically to find
the binding (usually longer) stream. This spot-check is good enough to
*flag* over/under-provisioning, but not thorough enough to *lock in* new
numbers on its own.

**Before applying**: rebuild one mode's dataset with the candidate value
(`dataset_builder.py --duration <candidate>`) and re-run
`kde_shape_check.py`. The whole point of shortening a duration is fewer
padding-dominated windows, so `nonzero_frac_global` should go **up**, not
down or stay flat, when you do this. If it doesn't move as expected, the
shorter window may be trimming real signal rather than padding, and the
candidate value needs reconsidering rather than applying as-is. This is a
cheap, fast check (minutes, not hours) — no reason to skip it before
touching a config value that every subsequent training run depends on.

## Why this is Tier 3 and not applied now

`config/kde_params.py` is *not* imported by anything the live campaign
touches (`collector/` or `scripts/run_*.sh`) — that's the entire reason it
was split out from `config/hyperparams.py` in the first place. So this
change is technically safe to apply even while collection is live. It's
queued here anyway, in keeping with the task's blanket rule of not applying
*any* change without a deliberate pause, and because the caveat above means
it genuinely shouldn't be applied blind regardless of live-safety.
