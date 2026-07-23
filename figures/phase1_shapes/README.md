# Phase 1 — Shape plots (vpn / tor / nym2 / nym5)

Built from the campaign's actual merged datasets (`wire-adversary-correlator-data`
GitHub repo, `datasets/{mode}/{mode}_merged.npz`), not pilot/test data. Neither
leroy nor the running nym5 collection fleet was touched to produce these —
all data pulled read-only from the pushed GitHub datasets and raw visit
`.jsonl` metadata (sparse-checkout, pcaps excluded).

## Parameters actually used — and why they differ from the paper / from an earlier spec

The initial request for this figure set specified the ShYSh paper's stated
values (σ=0.125s uniform, d=60s uniform, l=30, 50% overlap, shape normalized
to sum = N_F). **Those are not what the live pipeline uses.** Verified
directly against the code before building anything:

- `preprocessing/quartet_builder.py` imports `KDE_PER_MODE` from
  **`config/kde_params.py`** — the parameters actually applied to every visit.
- `config/hyperparams.py` also defines a `KDE`/`KDE_PER_MODE` dict with the
  same names, but it is **dead code for duration/sigma**: nothing in the
  quartet/shape-building path reads it for those two fields (it's only
  consulted, incidentally, for `min_packets`, where its values happen to
  still agree with `kde_params.py`). `config/hyperparams.py`'s duration
  values (30/60/60/30s) are leftovers from before the file was split and do
  **not** describe what built this data.
- Normalization: the merged datasets these figures were built from were
  produced while `preprocessing/kde.py`'s `kde_shape()` set
  `sum(shape) == n_samples` (the KDE grid length) instead of
  `sum(shape) == N_F` (packet count, ShYSh Eq. 3). **This was not a
  deliberate design decision** — checked against git history before writing
  this: the n_samples convention was introduced in commit `cf7735677`
  (2026-04-29), whose commit message describes an unrelated bundle of
  features (5-mode coordinator, nym2 WireGuard capture, port-per-mode
  egress isolation, pre-flight checks) and says nothing about
  normalization. The only place a "scale-invariance" rationale appeared was
  a docstring comment written as part of that same commit, with no design
  doc, issue, or other commit corroborating it — and it directly
  contradicts `PIPELINE_AUDIT.md` (2026-04-17, 12 days earlier), which had
  already reviewed and confirmed the *original* `sum(shape) == N_F`
  behavior as correct. This is drift silently riding along in an unrelated
  commit, not a co-designed choice, and it has since been reverted: commit
  `d983d82` (2026-07-23) restores `sum(shape) == N_F` per ShYSh Eq. 3, and
  additionally fixes a real correctness bug in that formula (N_F now
  counts only packets within `[0, duration)`, not every carved timestamp).
  **Consequence for this figure set specifically: the merged datasets
  pulled to build the plots below still reflect the old, reverted
  n_samples convention** — amplitude in every plot does not encode packet
  volume (see the separate packets-per-flow plots for that) — and these
  figures will need regenerating from a rebuilt dataset once one exists
  under the current (`d983d82`) code.

Real, live, per-mode parameters (`config/kde_params.py`, matches thesis
Table 4.2 exactly):

| mode | σ (s) | d (s) | min_packets | windows (confirmed from actual `.npz` shape) |
|---|---|---|---|---|
| vpn  | 0.125 | 6  | 50 | 3  |
| tor  | 0.25  | 32 | 50 | 21 |
| nym5 | 0.5   | 30 | 50 | 19 |
| nym2 | 0.2   | 30 | 5  | 19 |

Shared across all modes: `t_sample=0.1s`, `window_len=30` samples, `overlap=50%`.

**On tor's d=32s**: the thesis flags an open TODO — was the *entire* current
tor dataset rebuilt at d=32s, or does some of it still reflect an earlier
24s window? `git log` on `config/kde_params.py` shows only one commit
(2026-07-12, already at 32.0 — the 24→32 retune predates version control
here, so it can't be diffed directly). Resolved a different way instead:
`dataset_builder.py` hard-fails ("Inconsistent window counts across visits")
if any two visits in a merge produce different window counts, and the
current tor merge succeeded with a uniform 21 windows across every
audit-passed round. Working the padding-boundary formula backwards, 21
windows only occurs for `d` in the narrow range `(31.5s, 33.0s]` — which
excludes the old 24.0s value (that gives 15 windows) and is consistent with
the current 32.0s. **Confirmed**: every round in the current merged tor
dataset used d=32s; none reflect the old 24s window.

## Caveats (also annotated directly on the figures, not just here)

- **nym2 comparability**: `min_packets=5` vs. the global 50 used by
  vpn/tor/nym5 — 10x more permissive, because nym2's upstream is
  legitimately sparse. nym2 flows are filtered more loosely; not a
  fair like-for-like against the other three modes.
- **nym5 is incomplete**: collection is still running. Figures use whatever
  was in the merged dataset at pull time — **22,046 pairs**. Will grow.
- **Audit-passed rounds only**, matching what `merge_and_stage_mode.sh`
  actually includes:
  - vpn / tor: `campaign/round_01-03` + `campaign_fast/round_04-11`
  - nym2: `campaign/round_01-03` + `campaign_fast/round_04-07`
  - nym5: `campaign/round_01-03` + `campaign_nym5/round_04-07`
    (round_06 included via its manual audit-surgery pass, see
    `data/campaign_nym5/round_06/SURGERY_NOTE.txt` in the data repo)
- **Amplitude ≠ packet volume**: normalization is to grid length, not
  packet count (see above) — don't read shape height as traffic volume.
  Use figures `03_aggregate_*` for actual volume (packets/flow, from raw
  visit metadata, independent of the KDE arrays).
- **Cross-mode figure (04) deliberately does not share a time axis or
  y-scale** across panels. Forcing one would make vpn (d=6s) look
  empty next to tor (d=32s) purely as an artifact of per-mode tuning, not
  a real difference in correlatable structure. σ also varies 4x across
  modes (0.125→0.5s) — smoothness differences between panels are partly
  kernel width, not purely traffic character. An apples-to-apples
  "everyone recomputed at identical σ/d" figure was in scope as an optional
  extra, but requires re-deriving shapes from raw packet timestamps, which
  aren't available outside leroy's pcaps — skipped rather than faked from
  the pre-windowed data, consistent with "neither touches leroy."

## Figures

- **`01_paired_example_{mode}.pdf`** — headline plot (mirrors paper Fig. 2).
  3 visits per mode, entry (ingress) vs. exit (egress) downstream payload
  flows stacked, plus the corresponding ACK (upstream) flows, own time axis
  per mode. Selection method (stated on the figure): the 3 pairs whose
  ingress packet count is closest to that mode's median — not cherry-picked,
  and the selection criterion is independent of the (volume-blind) KDE shape
  itself.
- **`02_paired_vs_unpaired_{mode}.pdf`** — one genuinely paired tuple (same
  visit) directly above one unpaired tuple (same entry flow, exit flow from
  an unrelated visit with a different URL where possible), same style, so
  the visual difference is immediate.
- **`03_aggregate_{mode}.pdf`** — packets-per-flow and visit-duration
  histograms per mode, built from raw visit `.jsonl` metadata (not the
  normalized KDE arrays, which don't preserve volume information). The KDE
  window `d` is marked on the duration histogram to show how much of each
  mode's real duration distribution the window actually covers.
- **`04_cross_mode_comparison.pdf`** — one typical example per mode, all
  four in one figure, each on its own time axis and y-scale (see caveat
  above for why), with σ/d/window-count/min_packets annotated on every
  panel plus the nym2 and nym5 caveats inline.

## Reconstructing the plotted signal from the stored windowed arrays

The merged `.npz` files store overlapping windows (`X_ingress_up` etc.,
shape `(N, n_windows, 30)`), not the continuous pre-window signal. Every plot
here reconstructs the continuous curve by de-windowing: each sample is the
average of every window that covers it. Since windowing only *slices* the
already-normalized signal (no per-window renormalization) and overlapping
windows share the same underlying values, this reconstruction is exact, not
approximate.
