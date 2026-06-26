# Nym flow-correlation campaign — run order

Target: 25,000 valid flows/mode across vpn/tor/nym5/nym2, within a 20-day hard
license cap (~10 days expected).

All commands below run **on leroy**, from the repo root, inside a `tmux` session
so the campaign survives you disconnecting.

## Per-mode URL design

vpn/tor collect against the **full validated list** (500 URLs). nym5/nym2
collect against a **lighter subset**: the html+json URLs only (265 of the
500) — heavy mp3/mp4/pdf/zip are too slow/timeout-prone through nym5's 5-hop
path (`NS_ERROR_NET_TIMEOUT` observed in testing). The light list is a STRICT
SUBSET of the full list, derived automatically by `validate_urls.sh` (no
re-fetch — these URLs already passed all 4 targets).

**Why not just trim the full list's existing stages?** Light/heavy URLs sort
into contiguous alphabetical blocks by naming convention (`archive_zip_*`,
`audio_mp3_*`, `crypto_*.json`, `doc_pdf_*`, `page_*.html`, `video_mp4_*`) —
they are NOT evenly interleaved. On the real 500-URL list, the val split (75
URLs) happens to be **100% light** while train/test are unevenly mixed.
Filtering the full list's positional 50-URL chunks down to light URLs would
leave nym5/nym2 completely idle for several stages, then overloaded for
others. Instead: **two independent stage grids** (full and light), each
chunked into ~50-URL stages on its own URL count — see "Two grids, one round
counter" below.

## ⚠️ Light-list visits/URL decision — REQUIRED before launch

**265 light URLs × 50 visits/URL (matching vpn/tor's cadence) = 13,250 flows,
not 25,000.** Hitting 25k on 265 URLs needs ~94-95 visits/URL total
(25000 / 265 = 94.3), i.e. **47-48 visits/client/URL** (2 clients), not 25.

| Option | `VISITS_LIGHT` (per client/URL) | Total visits/URL | Flows/mode (265 URLs) |
|---|---|---|---|
| Match vpn/tor cadence | 25 | 50 | **13,250** (52% of target) |
| Hit 25k, round down | 47 | 94 | 24,910 |
| Hit 25k, round up | 48 | 96 | 25,440 |

There is **no default** — `scripts/run_stage.sh` and `scripts/run_campaign.sh`
both refuse to launch any light-list (nym5/nym2) stage unless `VISITS_LIGHT`
is set explicitly:

```bash
export VISITS_LIGHT=48   # or 47, or 25 if you're accepting ~13k for nym — YOUR call
```

This also means nym5/nym2 will need proportionally longer per-round
collection time than vpn/tor at the same URL-count-per-stage — factor that
into the budget tracker's per-round read, not just the final total.

## Locked parameters (everything else)

- vpn/tor: 500 URLs × 25 visits/client/URL = 25k flows/mode.
- nym5/nym2: 265 URLs × `VISITS_LIGHT` visits/client/URL (see above).
- `--rotate-every 3` for nym5/nym2 (measured throughput win — prior session).
  vpn/tor rotate every visit (`--rotate-circuits`, no `--rotate-every`).
- Staging: ~50 URLs/stage within each split, independently per list (11
  stages for the full list, 7 for the light list on the real data — see
  "Two grids" below).
- Whichever modes have an active stage in a given round run concurrently,
  both clients each (verified zero router drops at 8-way concurrency over a
  21h run; light-only or full-only rounds run at lower concurrency once one
  grid is exhausted).

## Run order

```bash
# 0. Load the campaign SSH key into a leroy-resident agent (NOT forwarded from
#    your laptop — the campaign opens fresh SSH connections constantly; a
#    forwarded agent dies the moment your laptop disconnects, killing every
#    subsequent reconnect for the rest of the run). Do this once per leroy
#    session/boot.
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/nico-thesis
ssh-add -l   # confirm it's loaded

# 1. STAGE 0 — URL validation. Run this FIRST. Tests every candidate URL
#    against the real endpoint each mode hits (not a full Playwright visit —
#    a quick direct curl, checking HTTP 200 + a size floor). Also derives the
#    light (html+json) subset automatically.
tmux new -s campaign
cd /volume1/scratch/r1086364/wire-adversary-correlator
source .venv/bin/activate
bash scripts/validate_urls.sh config/urls.txt data/campaign/stage0

# 2. REVIEW before proceeding:
cat data/campaign/stage0/validation_report.txt
#    - If fewer than 500 URLs passed: YOUR decision — collect fewer, or fix
#      the web server and re-run step 1.
#    - Confirm the light count: wc -l data/campaign/stage0/validated_urls_light.txt
#      (expect 265 on the full real list; fewer if some html/json URLs failed
#      validation).

# 3. DECIDE VISITS_LIGHT (see section above) — no default, must be explicit.
export VISITS_LIGHT=48   # example — this is YOUR decision, not a default

# 4. Launch the campaign. <license_deadline> is the hard 20-day cap date —
#    used by audit_stage.sh's budget tracker every round.
bash scripts/run_campaign.sh \
    data/campaign/stage0/validated_urls.txt \
    data/campaign/stage0/validated_urls_light.txt \
    data/campaign \
    2026-07-16    # <- set to your actual 20-day deadline

# The orchestrator loops rounds automatically: run_stage.sh -> audit_stage.sh
# -> next round, halting (non-zero exit, no auto-proceed) on any audit red
# flag. Detach with Ctrl-b d; reattach anytime with `tmux attach -t campaign`.

# 5. If the campaign HALTS (it will print which round and why):
cat data/campaign/round_NN/router_drops.log    # if drops flagged
cat data/campaign/round_NN/ALERTS.log          # if alerts flagged
#    Review, fix, then resume from the same round (VISITS_LIGHT must still
#    be set in the environment):
VISITS_LIGHT=48 bash scripts/run_campaign.sh \
    data/campaign/stage0/validated_urls.txt \
    data/campaign/stage0/validated_urls_light.txt \
    data/campaign 2026-07-16 NN
```

## Two grids, one round counter

`scripts/_stage_slices.py` computes ONE canonical train/val/test split over
the **full** (superset) list — same arithmetic as `model/dataset.py`
(`sorted(set(urls))`, 70/15/15 from `config/hyperparams.py`'s `EVAL`). The
light list's per-URL split membership is a **lookup** into this same
assignment, never recomputed independently — this is what guarantees a URL
shared between the full and light lists lands in the same split for both,
which the later cross-mode comparison on shared URLs depends on. The script
writes `split_consistency_check.txt`, an explicit per-shared-URL audit trail
of this.

Each list is then chunked into ~50-URL stages **independently, within each
split** (never across a split boundary, for either list). On the real
500-URL list: full → 350/75/75 → 7+2+2 = **11 stages**. Light → 165/75/25 (the
html+json subset of each split) → 4+2+1 = **7 stages**
(`[50,50,50,15, 50,25, 25]`).

`scripts/run_campaign.sh` advances both grids by a single **round** index:
round *i* runs full-stage *i* (vpn/tor) concurrently with light-stage *i*
(nym5/nym2) via one `run_stage.sh` call — **these are not the same URLs**,
just the same round number. Once the shorter grid (light, 7 stages) is
exhausted, vpn/tor keep running alone for the remaining rounds. `run_stage.sh`
accepts `NONE` for either argument and only launches the modes with an active
stage that round.

## What's safe to interrupt and resume

- **Mid-round** (process killed, leroy rebooted): re-running `run_stage.sh`
  for the same round is safe — `coordinator.py`'s `run_dataset()` resumes by
  reading the existing `{mode}_visits.jsonl` and skipping already-successful
  `(url, visit_num)` pairs (unchanged, pre-existing behavior — see
  `collector/coordinator.py` `completed_counts` / "resuming: N/M visits
  already collected"). Visits already logged are never redone.
- **Round boundaries are the orchestrator's own checkpoint**: a round that
  wrote `.audit_passed` is skipped on re-run of `run_campaign.sh`. A round
  that failed audit is NOT marked passed and will re-run in full (still
  cheap — `coordinator.py`'s own resume means only the missing visits
  actually get collected).

## What's checked, every round, automatically

`scripts/run_stage.sh`:
- Refuses to launch if `~/.ssh/nico-thesis` isn't loaded in a running agent,
  if either router is unreachable, or (for any active light-list stage) if
  `VISITS_LIGHT` isn't set.
- A client unreachable at round start gets an `hcloud server reboot` +
  poll-until-reachable (up to 240s) attempt; if it doesn't come back, that
  client is skipped **for this round only** — the others still launch. A
  down client delays its mode, never aborts the round (this previously
  crashed the whole run with an uncaught `RuntimeError`).
- Samples router `rx_dropped`/`rx_missed` every 10 minutes for the round's
  duration into `router_drops.log`.
- Re-asserts the doubled-URL guard (bare paths only) on both the full and
  light slice files, even though `validate_urls.sh` already checked it, in
  case a slice file was hand-edited.

`scripts/audit_stage.sh`, after every round, before the next is allowed:
1. Per-mode yield (success vs error/`WEDGE_UNRECOVERABLE`) — flags <85%.
2. `ingress_packets` distribution per mode — flags any `success` record with
   `ingress_packets=0` (the zero-ingress guard should have already caught
   this; a flag here means it didn't).
3. Router drops across the round's samples — flags any non-zero.
4. Contamination sweep — every ingress pcap's `10.0.0.x` addresses checked
   against the owning client's own private IP (the per-client BPF scoping
   should already prevent this; this is the assertion that it actually did).
5. Wedge accounting per client — flags any `WEDGE_UNRECOVERABLE` or
   `ALERTS.log` entry; notes (not flags) if one client is wedging far more
   than the others (nym5-client1 has shown this pattern before).
6. Budget tracker — this round's realized flows/hour per mode, cumulative
   progress toward 25k/mode, projected days needed for the remainder vs.
   license days remaining. Prints `ON TRACK` / `TIGHT` / `OVER BUDGET`.

Any flag → exit 1 → campaign halts. No flag → `.audit_passed` written →
next round starts automatically.

## Fixed/built this session

- **Web-server preflight always failing (`HTTP 000000`)**: the check curled
  `URL_BASE[mode]` *from the egress router*, but for tor/nym5/nym2 that's the
  egress router's **own public IP** — a self/hairpin connection that fails
  outright on this infra before any HTTP response, so both curl's `-w`
  format string and the `|| echo 000` fallback fire, producing the
  doubled `"000000"`. Now checks the web server's **private IP** directly on
  the mode's actual nginx port instead — same content-correctness signal,
  no hairpin dependency.
- **Doubled-URL guard**: `validate_urls.sh` and `run_stage.sh` reject any
  candidate/slice file containing a full `http://...` entry before it can be
  silently concatenated onto a mode's `URL_BASE`.
- **Per-mode URL design + split-consistency**: see sections above.
