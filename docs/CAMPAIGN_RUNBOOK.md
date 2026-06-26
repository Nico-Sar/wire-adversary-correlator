# Nym flow-correlation campaign — run order

Target: 25,000 valid flows/mode across vpn/tor/nym5/nym2 (4 × 25k = 100k total),
from a shared 500-URL pool, within a 20-day hard license cap (~10 days expected).

All commands below run **on leroy**, from the repo root, inside a `tmux` session
so the campaign survives you disconnecting.

## Locked parameters

- 500 URLs × 50 visits/mode = 25k flows/mode. 2 clients/mode × 25 visits/client/URL.
- `--rotate-every 3` for nym5/nym2 (measured throughput win — see prior session).
  vpn/tor rotate every visit (`--rotate-circuits`, no `--rotate-every` needed).
- Staging: ~50 URLs/stage, split-aligned (see "Why 11 stages, not 10" below).
- All 4 modes run concurrently within a stage, both clients each (verified
  zero router drops at 8-way concurrency over a 21h run).

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
#    a quick direct curl, checking HTTP 200 + a size floor).
tmux new -s campaign
cd /volume1/scratch/r1086364/wire-adversary-correlator
source .venv/bin/activate
bash scripts/validate_urls.sh config/urls.txt data/campaign/stage0

# 2. REVIEW before proceeding:
cat data/campaign/stage0/validation_report.txt
#    - If fewer than 500 URLs passed: YOUR decision — collect fewer, or fix
#      the web server and re-run step 1.
#    - Check the per-extension breakdown: mp3/mp4/pdf/zip files that passed
#      the content check can still blow the per-visit time budget on
#      nym5/nym2's slow path (this validation does NOT test fetch time
#      through the anonymity network — see the note in the report). Decide
#      whether to hand-trim heavy extensions from data/campaign/stage0/validated_urls.txt
#      before proceeding, especially for nym5/nym2.

# 3. Launch the campaign. <license_deadline> is the hard 20-day cap date —
#    used by audit_stage.sh's budget tracker every stage.
bash scripts/run_campaign.sh \
    data/campaign/stage0/validated_urls.txt \
    data/campaign \
    2026-07-16    # <- set to your actual 20-day deadline

# The orchestrator loops stages automatically: run_stage.sh -> audit_stage.sh
# -> next stage, halting (non-zero exit, no auto-proceed) on any audit red
# flag. Detach with Ctrl-b d; reattach anytime with `tmux attach -t campaign`.

# 4. If the campaign HALTS (it will print which stage and why):
cat data/campaign/stage_NN/router_drops.log    # if drops flagged
cat data/campaign/stage_NN/ALERTS.log          # if alerts flagged
#    Review, fix, then resume from the same stage:
bash scripts/run_campaign.sh \
    data/campaign/stage0/validated_urls.txt data/campaign 2026-07-16 NN
```

## What's safe to interrupt and resume

- **Mid-stage** (process killed, leroy rebooted): re-running `run_stage.sh`
  for the same stage is safe — `coordinator.py`'s `run_dataset()` resumes by
  reading the existing `{mode}_visits.jsonl` and skipping already-successful
  `(url, visit_num)` pairs (unchanged, pre-existing behavior — see
  `collector/coordinator.py` `completed_counts` / "resuming: N/M visits
  already collected"). Visits already logged are never redone.
- **Stage boundaries are the orchestrator's own checkpoint**: a stage that
  wrote `.audit_passed` is skipped on re-run of `run_campaign.sh`. A stage
  that failed audit is NOT marked passed and will re-run in full (still
  cheap — `coordinator.py`'s own resume means only the missing visits
  actually get collected).

## Why 11 stages, not ~10

`model/dataset.py`'s train/val/test split is by URL, computed as
`sorted(set(urls))[:n_train]` / `[n_train:n_train+n_val]` / `[n_train+n_val:]`
using `EVAL["train_split"]`/`val_split` from `config/hyperparams.py` (0.70/0.15,
test gets the remainder). For 500 URLs that's exactly 350/75/75.
`scripts/_stage_slices.py` reproduces this exact arithmetic, then chunks
**within** each split independently (never across a split boundary) into
~50-URL stages: 350/50 = 7 exact stages, then 75/50 = one 50 + one 25 stage,
twice (val, test). Total: 7 + 2 + 2 = **11 stages**, sizes
`[50,50,50,50,50,50,50, 50,25, 50,25]`. No stage straddles a split boundary,
by construction — see `scripts/_stage_slices.py`'s manifest output for the
exact per-stage split assignment.

## What's checked, every stage, automatically

`scripts/run_stage.sh`:
- Refuses to launch if `~/.ssh/nico-thesis` isn't loaded in a running agent,
  or if either router is unreachable.
- A client unreachable at stage start gets an `hcloud server reboot` +
  poll-until-reachable (up to 240s) attempt; if it doesn't come back, that
  client is skipped **for this stage only** — the other 7 still launch. A
  down client delays its mode, never aborts the stage (this previously
  crashed the whole run with an uncaught `RuntimeError`).
- Samples router `rx_dropped`/`rx_missed` every 10 minutes for the stage's
  duration into `router_drops.log`.
- Re-asserts the doubled-URL guard (bare paths only) even though
  `validate_urls.sh` already checked it, in case a slice file was hand-edited.

`scripts/audit_stage.sh`, after every stage, before the next is allowed:
1. Per-mode yield (success vs error/`WEDGE_UNRECOVERABLE`) — flags <85%.
2. `ingress_packets` distribution per mode — flags any `success` record with
   `ingress_packets=0` (the zero-ingress guard should have already caught
   this; a flag here means it didn't).
3. Router drops across the stage's samples — flags any non-zero.
4. Contamination sweep — every ingress pcap's `10.0.0.x` addresses checked
   against the owning client's own private IP (the per-client BPF scoping
   should already prevent this; this is the assertion that it actually did).
5. Wedge accounting per client — flags any `WEDGE_UNRECOVERABLE` or
   `ALERTS.log` entry; notes (not flags) if one client is wedging far more
   than the others (nym5-client1 has shown this pattern before).
6. Budget tracker — this stage's realized flows/hour per mode, cumulative
   progress toward 25k/mode, projected days needed for the remainder vs.
   license days remaining. Prints `ON TRACK` / `TIGHT` / `OVER BUDGET`.

Any flag → exit 1 → campaign halts. No flag → `.audit_passed` written →
next stage starts automatically.

## Fixed this session (already in `collector/coordinator.py`)

- **Web-server preflight always failing (`HTTP 000000`)**: the check curled
  `URL_BASE[mode]` *from the egress router*, but for tor/nym5/nym2 that's the
  egress router's **own public IP** — a self/hairpin connection that fails
  outright on this infra before any HTTP response, so both curl's `-w`
  format string and the `|| echo 000` fallback fire, producing the
  doubled `"000000"`. Now checks the web server's **private IP** directly on
  the mode's actual nginx port instead — same content-correctness signal,
  no hairpin dependency.
- **Doubled-URL guard**: both `validate_urls.sh` and `run_stage.sh` reject
  any candidate/slice file containing a full `http://...` entry before it
  can be silently concatenated onto a mode's `URL_BASE`.
