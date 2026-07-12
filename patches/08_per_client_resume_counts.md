# Patch 08 — scope resume `completed_counts` per client, not pooled per URL

**Status: prepared (`patches/08_per_client_resume_counts.patch`), NOT applied.**
**Discovered live, 2026-07-12, during Phase 3 of the deliberate pause for patches 06/07.**

## Intent check (done before writing any code, per instruction)

Confirmed from `docs/CAMPAIGN_RUNBOOK.md` and commit `fbe267d` ("docs: record
VISITS_LIGHT=48 decision in campaign runbook"): **each nym5/nym2 client is
supposed to independently reach 48 visits/URL** — not the two clients
jointly covering 48 between them. The runbook's own arithmetic only closes
with independent quotas: "265 URLs × 48 visits/client/URL × 2 clients =
25,440 flows/mode" (the documented target). Pooling would land at half
that. This confirms the fix below is the correct one, not a doubled-effort
misdiagnosis.

## The bug

`run_dataset()` builds `completed_counts` (a `url → success count` dict) by
reading the mode's log file (`nym5_visits.jsonl` / `nym2_visits.jsonl`) once
at process start, to decide which `(url, visit_num)` pairs to skip on
resume. That log file is **shared** — both clients of a mode append to the
same path — but the counting didn't distinguish which client a given
success belonged to, so it summed **both clients'** successes per URL.

This was invisible for the entire life of a continuously-running coordinator
process: the resume-skip check only reads the file once, at that process's
own startup, and after the initial skip-forward it just keeps issuing real
new visits up to its own `visits_per_url` bound without re-checking the file
again — so two clients that both started early in a round (with low
combined counts at the time) each independently ground out their own real
48/URL over the following days, oblivious to each other.

It surfaces the moment a client is **restarted** after its peer has already
finished: the freshly-read pooled count is now `client_A + client_B`, which
exceeds 48 for nearly every URL even if `client_B` alone is nowhere close.
Confirmed live in round_03 right after resuming from this pause's Phase 1
stop: nym5-client1 had genuinely finished (47-48/URL), nym5-client2 had only
done 15-21/URL of its own — but the pooled per-URL total (62-67) was already
past 48, so **both** clients immediately printed "already collected —
skipping" for every visit and exited within seconds, having done zero new
work. This then cascaded: `run_stage.sh`'s backfill monitor (watching for
"both nym5 PIDs exited" as its stop signal) fired almost immediately,
halting vpn/tor/nym2's backfill loops too — so the restart briefly appeared
to complete a whole round while collecting essentially nothing.

## The fix

One condition added to the existing per-line loop that already builds
`completed_counts`: only count a `"success"` record toward this client's
own completed_counts if its `visit_id` actually belongs to `client_id`
(`vid.startswith(f"{client_id}_")` — visit_ids are always
`{client_id}_v{serial}` or `{client_id}_bf{serial}`, an exact,
unambiguous prefix match). Also reworded the "resuming: N/total" print to
say "THIS client's visits" so the log doesn't misread as a mode-wide count
again in the future.

**Not changed**: `serial` itself stays computed across the whole shared
file (both clients' entries) — that part is correct as-is and must stay
shared, since it's what keeps `visit_id`s unique across both clients
writing to the same log path. Only the resume-skip *count* needed scoping,
not the ID-uniqueness counter.

## Why this is a proper patch, not a live edit

Applied on top of the already-verified-applied patches 06 and 07 (same
file, `collector/coordinator.py`), during the same deliberate pause. Not
applied directly — syntax-checked and dry-run-verified to apply cleanly on
top of 06+07's current state (see verification: applying it to a copy of
leroy's live post-06/07 file byte-for-byte reproduces this patch's intended
result, and the result compiles).

## Risk

Low, narrowly scoped — one added boolean condition on an existing filter,
no new control flow, no change to any other function. The only behavior
change is which pre-existing log lines count toward a client's own resume
point; it cannot cause a client to under-count (skip too little) since it
only *removes* entries from consideration (a stricter filter), never adds
ones that weren't already being read.

## How to apply, at the next deliberate pause

```bash
patch -p0 < patches/08_per_client_resume_counts.patch
python3 -m py_compile collector/coordinator.py
rsync -av collector/coordinator.py leroy:/volume1/scratch/r1086364/wire-adversary-correlator/collector/coordinator.py
```
Same restart requirement as 06/07 — a running coordinator has already
imported the old module, so this needs the campaign stopped and every
coordinator relaunched, not a live rsync alone.
