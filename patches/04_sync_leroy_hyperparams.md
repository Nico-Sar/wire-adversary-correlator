# Patch 04 — remove leroy's stale `KDE_PER_MODE` dict from `config/hyperparams.py`

**Status: prepared, NOT applied.**

## What's wrong

The 2026-07-12 campaign audit found that leroy's checked-out copy of
`config/hyperparams.py` still contains an inline `KDE_PER_MODE` dict with the
old, un-retuned durations (vpn=30s, tor=60s, nym5=60s) — left over from
before KDE tuning was split out into `config/kde_params.py` (see that file's
own docstring for why the split happened: avoiding a torn-read risk on
`VISIT_TIMEOUTS`, which `collector/coordinator.py` imports live).

This is **dead code, not a live risk**: `preprocessing/dataset_builder.py`
and every other consumer imports `KDE`/`KDE_PER_MODE` from
`config/kde_params.py`, never from `config/hyperparams.py`. Confirmed the
local (lex) copy of `config/hyperparams.py` is already clean — it only has
`VISIT_TIMEOUTS`, `MODEL`, `COLLECTION`, `EVAL`, no `KDE_PER_MODE` at all.
So this isn't a patch against the local file (there's nothing stale here to
remove) — it's leroy's copy that has drifted from local and needs a sync.

## Why this is Tier 3 ("prepare, don't apply") and not just done immediately

`config/hyperparams.py` is exactly the file `collector/coordinator.py`
imports live (`VISIT_TIMEOUTS`) during collection — the file this whole
KDE-params split was designed to keep untouched while a round is running.
Even though this specific change only removes an unused dict and wouldn't
plausibly break `VISIT_TIMEOUTS`, editing this file while the live campaign
process holds it open is exactly the class of action the CRITICAL SAFETY
RULE prohibits without a deliberate pause — so it's queued here rather than
applied.

## How to apply, at the next deliberate pause

Simplest and safest: just overwrite leroy's copy with the current local
(lex) one, which is already correct — no manual editing on leroy needed:

```bash
rsync -av config/hyperparams.py leroy:/volume1/scratch/r1086364/wire-adversary-correlator/config/hyperparams.py
```

Then confirm on leroy that the file still imports cleanly (matches the
lesson from the 2026-07-01 torn-read incident — always verify after touching
anything a live process reads):

```bash
ssh leroy 'cd /volume1/scratch/r1086364/wire-adversary-correlator && .venv/bin/python3 -c "from config.hyperparams import VISIT_TIMEOUTS; print(VISIT_TIMEOUTS)"'
```
