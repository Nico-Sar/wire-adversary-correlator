# Patch 10 (design only) — run nym5 as its own campaign instance

**Status: INVESTIGATION + DESIGN ONLY. No code written, nothing applied,
nothing touched live. Supersedes/refines `01_round_barrier_decoupling.md`'s
"two loops" sketch with a full router-sharing and audit-marker collision
analysis, and a concrete recommended design.**

**Verdict up front: CLEAN enough to apply in one reviewed pause — provided
the two instances use separate `campaign_root` directories (Design A
below), not a shared round-dir namespace. That one design choice is what
separates "safe" from "the single most dangerous part," detailed in
Risk §1.**

## 1. Coupling map

### The exact barrier

`scripts/run_stage.sh`, lines 298-305:
```bash
log "Waiting for all launched clients to finish..."
FAILED_CLIENTS=()
for client_id in "${!PIDS[@]}"; do
    if ! wait "${PIDS[$client_id]}"; then
        log "WARNING: $client_id exited non-zero"
        FAILED_CLIENTS+=("$client_id")
    fi
done
```
This is unconditional — it blocks on **every** launched client's PID
(vpn/tor/nym5/nym2 alike) before `run_stage.sh` returns. `run_campaign.sh`
then runs `audit_stage.sh` and only touches `.audit_passed` — the single
gate its own round loop checks before advancing — after `run_stage.sh` has
already returned. So: round N cannot close for **any** mode until nym5,
specifically, finishes round N.

### Modes are already separable at the launch level

`run_stage.sh`'s `launch_client()` spawns each client as an independent
backgrounded `collector.coordinator` process with its own SSH connections
directly to the routers — there's no cross-client coordination inside
`coordinator.py` at all (confirmed while reading it extensively across
patches 06-09 this session: every recovery/rotation/capture function takes
a single client's SSH handle and touches nothing shared except the two
router connections and the collection lock, which are already per-client-
or per-router-connection scoped). **The coupling is 100% at the
orchestration layer** (`run_campaign.sh`'s single round loop +
`run_stage.sh`'s single wait barrier), not in the collection code itself.
This is good news — it means none of patches 06/07/08/09 need to change at
all for this split.

### Shared vs. per-mode state

| State | Scope today | Collision risk if split naively |
|---|---|---|
| `{mode}_visits.jsonl` | already per-mode filename, shared round dir | none — never was shared content |
| `log_{client_id}.txt` | already per-client filename | none |
| `.audit_passed` | **one marker per round dir, covers all modes** | **HIGH — see §1 below** |
| `stage_meta.txt` | one file per round dir, all modes' launch/skip/fail recorded together | low (see §1) |
| `backfill_stop` | one file per round dir, consumed by vpn/tor/nym2's coordinator processes | moot if backfill is removed (see §3) |
| `router_drops.log` | one file per round dir, appended by a background loop in `run_stage.sh` | none if each instance has its own round-dir tree (append-only, and `check_router_drops.sh snapshot` is read-only against the router — verified, see §2) |
| the routers themselves (ingress/egress tshark, SSH sessions) | shared physical hosts, always | **investigated in depth, LOW risk — see §2** |
| `_url_slices/{full,tor,light}/stage_NN.txt` | precomputed once, read-only from here on | none — never mutated after `_stage_slices.py` runs |
| the global train/val/test split assignment | computed once, baked into the stage file contents | none — see §1's split-independence finding |

### Round/split mapping does NOT depend on modes advancing together

This is the load-bearing finding. From `scripts/_stage_slices.py`:

- The train/val/test split is assigned **once, per URL**, over the full
  (superset) list (`assign_global_split()`), independent of any round
  number.
- Each grid (full / tor / light) is chunked into `stage_NN.txt` files
  **independently**, bucketed by split first, then chunked within each
  bucket. The script's own docstring states this explicitly: *"stage N is
  not the same URLs as full/tor stage N"* — i.e., "round 8" already means
  something completely different for the full grid vs. the light grid
  **today**, in the current single-instance system. `run_campaign.sh`'s
  shared round counter is a loop-iteration convenience, not something the
  data model depends on.

**Consequence**: the light grid can be consumed at any pace, in any real-
time relationship to the full grid, and split-consistency is untouched —
it was never encoded in round numbers, only in the pre-computed stage file
contents. Nothing about the split logic needs to change for this split.

## 2. Router-sharing risk — the question you specifically flagged

Traced through every router-touching code path in `coordinator.py` and the
orchestration scripts:

- **`ensure_router_ssh()` (patch 07)** and every per-visit SSH connection to
  the routers are opened **directly by each client's own `coordinator.py`
  process**, never proxied or coordinated through `run_stage.sh`/
  `run_campaign.sh`. Today, 8 concurrent clients already open 8 concurrent
  independent SSH sessions to each router from **one** orchestrator. Two
  orchestrators managing, say, 2 and 6 clients respectively produce
  **exactly the same router-side traffic pattern** — same session count,
  same tshark invocations, same everything. The router cannot tell the
  difference between "one orchestrator running 8 clients" and "two
  orchestrators running 2+6 clients" — from its perspective there's no such
  thing as an "orchestrator," only individual SSH sessions per client.
  **This means splitting into two instances does not change router-level
  concurrency at all.** Low risk, confirmed by tracing the code, not by
  assumption.
- **`check_router_drops.sh snapshot`** (the periodic drop-sampling loop
  `run_stage.sh` backgrounds): read `ip -s link show` and `/proc/loadavg` —
  both are read-only kernel counter reads (cumulative, never reset by
  reading). Two concurrent invocations (one per instance) simply both read
  the same counters independently and write to their own separate output
  files. No collision possible.
- **The one real pre-existing hazard, unrelated to this split**:
  `start_remote_capture()` in `coordinator.py` runs this backstop on every
  single capture start, on both routers:
  ```python
  "for p in $(ps -eo pid,etimes,comm | awk '$3==\"tshark\" && $2>120 {print $1}'); "
  "do kill -9 \"$p\" 2>/dev/null; done"
  ```
  This is a **global** kill — it matches any process named `tshark` older
  than 120s, not scoped to which client or orchestrator started it. The
  comment claims "120s is far above any real visit's capture window," but
  nym5's own `browser_ms` timeout is 180,000ms (180s) and nym2's is
  120,000ms (120s) — both can legitimately exceed 120s of capture time.
  **This is a latent, pre-existing bug that already exists today** with 8
  clients sharing one instance; splitting into two orchestrators doesn't
  create it and doesn't make it meaningfully worse (same total client
  count hitting the same routers either way) — but it's worth fixing
  separately at some point (e.g. raise the threshold, or scope the kill to
  exclude the caller's own just-launched PID). Flagging it here since it's
  exactly the kind of thing your question was probing for, even though the
  split itself doesn't change its risk profile.
- **Per-client capture directories and cleanup** (`mkdir -p
  .../captures/{client_id}` at start, `rm -rf .../captures/{client_id}` in
  `run_dataset`'s `finally`) are already client_id-scoped — since the two
  instances would never both launch the same client_id, no collision.
- **hcloud reset locking** (`/tmp/hcloud_reset_{client_id}.lock`, used by
  both `run_stage.sh`'s bash-side `ensure_client_reachable()` and
  `coordinator.py`'s Python-side `_hcloud_reset()`) is already designed for
  multi-caller safety per client_id via `flock` — unaffected by adding a
  second orchestrator, for the same reason as above.

**Bottom line on router sharing: it's the part you were most worried about,
and it turns out to be the least risky part of this whole change.**

## 3. The actual dangerous part: `.audit_passed` if round-dir numbering is shared

If the two instances were to share `data/campaign/round_NN/` naming (each
keeping its own round counter but writing into a directory named by that
same counter), here's the concrete failure: `run_campaign.sh`'s skip check
is purely presence-based —
```bash
if [[ -f "$round_out/.audit_passed" ]]; then
    log "Round $round already passed audit — skipping"
    continue
fi
```
`.audit_passed` doesn't record *which modes* it certified. If the fast
instance reaches its own "round 8" first (likely, since it's the faster
grid) and its audit passes with only vpn/tor data present (nym5/nym2 not
yet run into that same directory), it touches `round_08/.audit_passed`.
When the light instance *later* reaches its own "round 8," it would see
that marker already present and **skip the round entirely — never
collecting nym5/nym2 data for it at all.** Silent data loss, no error,
because the marker doesn't distinguish which grid certified it.

`patches/01_round_barrier_decoupling.md`'s original sketch waved past this
("the existing directory scheme already tolerates" different rounds being
written at different times) — that's true only as long as the two loops'
round numbers never actually coincide in real time, which isn't guaranteed
and is exactly the kind of assumption that shouldn't be load-bearing for a
data-integrity guarantee.

**The fix is structural, not a patch to `audit_stage.sh`'s logic: give each
instance its own `campaign_root` directory entirely** (e.g.
`data/campaign_nym5/` and `data/campaign_fast/`). Then `round_08` in one
tree and `round_08` in the other are different filesystem paths — the
`.audit_passed`-collision scenario becomes structurally impossible, not
just unlikely. `audit_stage.sh` needs **zero changes** for this — it
already gracefully skips modes not present in a given `$STAGE_DIR` (every
per-mode check loop already has a `mode_expected()`/file-existence guard),
so each instance can call it unmodified against its own root.

## 4. Design (Design A — recommended)

- **Two independent `campaign_root` trees**: `data/campaign_nym5/` (nym5-
  client1, nym5-client2 only) and `data/campaign_fast/` (vpn×2, tor×2,
  nym2×2 — nym2 travels with the "fast" instance since it already collects
  against the light URL list directly and isn't gated by nym5's pace, only
  by the shared barrier this split removes).
- **Both instances read the same, unmodified `_url_slices/` output** from
  `_stage_slices.py` — that script doesn't need to change; it's already
  computed once, read-only from here on, and its split-independence (§1)
  means both instances can consume it at completely different paces safely.
- **`run_campaign.sh` needs one small change**: a way to force one grid to
  `NONE` unconditionally, regardless of whether that grid's stage files
  exist on disk (today it only passes `NONE` when a stage file is
  literally *absent* — necessary because both grids' files always exist
  once `_stage_slices.py` has run). Cleanest version: an explicit
  `--modes=light` / `--modes=full,tor` flag (or two thin wrapper scripts)
  that skips computing/launching the other grid entirely. Small, localized
  change — the round loop, `.audit_passed` check, and `audit_stage.sh` call
  stay structurally identical, just parameterized by which grid.
- **`run_stage.sh`**: same small change — accept a mode-scope so it never
  populates `CANDIDATES` for the excluded grid. No change needed to
  `ensure_client_reachable`, the router-drop monitor, or the client launch
  logic itself.
- **Backfill subsystem: remove it**, per your own note and confirming
  `01_round_barrier_decoupling.md`'s original finding. It exists
  specifically to keep vpn/tor/nym2 busy while trapped waiting on nym5
  within a shared round. Once they're not trapped, there's nothing to fill
  time with — they just advance to their own next round instead. This
  deletes code (`run_stage.sh`'s §3b/3c, the `BACKFILL_STOP` sentinel, the
  monitor subshell, and `coordinator.py`'s `--backfill-urls`/
  `--backfill-stop-file` handling in `run_dataset`) rather than adding any,
  which is a net risk reduction, not just a simplification — it's also the
  subsystem responsible for this session's earlier `BACKFILL=1`-omission
  incident, so removing it removes that failure mode entirely.
- **Transition detail to work out at implementation time, not blocking**:
  the *existing* `data/campaign/round_01/`, `round_02/`, `round_03/`
  (current, in-progress, mixed-mode) data stays exactly as it is —
  untouched historical record. The two new instances start fresh
  `campaign_root` trees going forward. The one thing to decide during
  implementation: whether nym5's *first* round in its new tree should point
  at the *existing* `round_03` directory (to avoid re-collecting the
  ~2,400+ visits already sitting there) or start a clean `round_01` in the
  new tree and accept some redundant re-collection of already-covered
  URLs. Given `coordinator.py`'s resume logic (patch 08) is already
  correctly per-client and per-URL, pointing the new nym5 instance's first
  invocation at the existing `round_03` directory as its output path is
  the more efficient option and costs nothing extra to implement — just a
  decision, not new code.

## 5. Risk rating: **LOW-MEDIUM**

- Router sharing: **LOW** (§2 — traced, not assumed; genuinely unchanged
  from today's already-proven-working reality).
- Round-dir/audit separation: **LOW, conditional on Design A** (separate
  `campaign_root` trees) — this is the one place a wrong design choice
  (shared round-dir namespace) would push this to HIGH. Design A makes the
  collision structurally impossible rather than merely unlikely.
- Backfill removal: **LOW** (net code deletion, removes a subsystem that
  already caused one real incident this session).
- `coordinator.py`: **NONE** — no changes needed, patches 06/07/08/09 stay
  exactly as they are.
- New surface area: two small, well-scoped changes to `run_campaign.sh` and
  `run_stage.sh` (a mode-scope parameter) — this is meaningfully smaller
  than patch 01's original "two loops in one script" sketch, since each
  instance is just an unmodified invocation of the (slightly parameterized)
  existing scripts rather than a rewritten internal loop structure.

**Single most dangerous part, if this is ever built carelessly**: skipping
the separate-`campaign_root` requirement and trying to share round-dir
numbering to "keep things tidy" — that's the one design choice that turns
this from safe into a silent-data-loss risk. Flagging this explicitly so
it doesn't get lost between design and implementation.

## 6. Rollback

If the split misbehaves after applying: stop both instances, the two
`campaign_root` trees are additive and never touch or delete the original
`data/campaign/` tree, so reverting is just going back to invoking the
original (unmodified) `run_campaign.sh` against the original
`data/campaign/` root — no data migration to undo, since none was
performed. The mode-scope flag is opt-in (default behavior with no flag
should stay "process both grids," matching current behavior exactly),
so an unpatched invocation continues to work identically if needed as a
fallback.

## 7. Apply plan sketch (for a future, separate implementation session)

1. Deliberate pause (same protocol as patches 06-09): stop the current
   single-instance campaign cleanly, snapshot for rollback.
2. Implement the mode-scope flag on `run_campaign.sh`/`run_stage.sh`,
   remove the backfill subsystem, syntax-check.
3. Decide and execute the round_03-continuation detail from §4.
4. Launch the two instances (two tmux windows/sessions), each with its own
   `campaign_root`.
5. Verify: both instances' clients start correctly, both write into their
   own directory trees (spot-check no cross-writes), both audit gates work
   independently, router drop monitors from both instances show clean
   deltas, no unexpected tshark backstop kills during the first hour of
   overlap.
6. Estimated implementation + apply + verify time: roughly on the same
   order as the 06-09 patch session (a few hours including live
   verification), smaller in code surface than patch 01's original "two
   loops" sketch would have been.

## Verdict

**CLEAN enough to apply in one reviewed pause**, provided Design A
(separate `campaign_root` trees) is the one actually built — not a
shared-round-dir variant. No further design work needed before an
implementation session; the router-sharing concern that motivated deeper
investigation turned out to be low-risk once traced, and the one genuine
hazard (audit-marker collision) has a clean structural fix already
identified.
