---
name: Pilot dataset hard-negative validation
description: Per-URL visit counts in pilot NPZ files — confirms hard negatives are fully functional
type: project
---

Validated 2026-04-21 via `model.dataset.validate_hard_negatives()`.

| Dataset               | URLs | min visits | Single-visit | Status |
|-----------------------|------|------------|--------------|--------|
| data/baseline_dataset.npz | 100 | 2 | 0 (0%) | OK |
| data/vpn_dataset.npz      | 100 | 3 | 0 (0%) | OK |
| data/tor_dataset.npz      | 100 | 2 | 0 (0%) | OK |
| data/nym_dataset.npz      |  70 | 2 | 0 (0%) | OK |

All URLs have ≥2 visits. Hard-negative sampling (5 hard + 5 soft per positive) is fully functional — no silent fallback to soft negatives is occurring.

**Why:** If a URL has only 1 visit, `n_hard_actual=0` and all negatives become soft, making training easier and inflating accuracy metrics vs. ShYSh's 1:5:5 design.

**How to apply:** Re-run `validate_hard_negatives()` whenever a new dataset is built. `MODEL["min_visits_per_url"]=2` enforces this via a hard error.
