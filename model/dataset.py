"""
model/dataset.py
================
PyTorch Dataset for Quartet flow pairs.
 
Positive pairs:  (ingress_windows, egress_windows) from the same visit → label 1
Negative pairs:  ingress from visit A, egress from visit B            → label 0
 
Negative sampling follows ShYSh: neg_pos_ratio negatives per positive, with
hard_neg_frac of them drawn from the same URL (hard negatives) to prevent
the model from learning URL fingerprints instead of flow shapes.
 
Handles two .npz formats:
  Full format  (dataset_builder.py output):
    keys: X_ingress_up, X_ingress_down, X_egress_up, X_egress_down,
          ingress_visit_ids, egress_visit_ids, ingress_urls, egress_urls,
          pairs (N×3 int32: [ingress_idx, egress_idx, 1]), modes
    Ingress and egress arrays are independently shuffled; 'pairs' encodes
    the correspondence.
 
  Simplified format (test fixtures):
    keys: X_ingress_up, X_ingress_down, X_egress_up, X_egress_down,
          visit_ids, urls, modes
    Implicit row alignment: row i ingress corresponds to row i egress.
 
Split strategy: 70 / 15 / 15 by URL rank (alphabetical), matching ShYSh.
At least 1 URL per split is guaranteed when the total URL count is ≥ 3.
"""
 
import numpy as np
import torch
from torch.utils.data import Dataset
 
from config.hyperparams import EVAL, MODEL


def validate_hard_negatives(npz_path: str, min_visits: int | None = None) -> None:
    """
    Print a visit-per-URL summary and raise if hard-negative sampling is impaired.

    Counts visits per URL in the .npz at npz_path and prints:
      - min / max / mean / median visits per URL
      - number and % of URLs with only 1 visit (these produce zero hard negatives)
      - warning if >5% of URLs are single-visit
    Raises ValueError if any URL has fewer than min_visits visits.
    min_visits defaults to MODEL["min_visits_per_url"] when not supplied.
    """
    import statistics
    from collections import Counter

    if min_visits is None:
        min_visits = MODEL["min_visits_per_url"]

    data = np.load(npz_path, allow_pickle=True)

    if "pairs" in data:
        pairs_arr = data["pairs"]
        urls      = list(data["ingress_urls"])
        pos_urls  = [urls[int(r[0])] for r in pairs_arr if int(r[2]) == 1]
    else:
        pos_urls = list(data["urls"])

    counts  = Counter(pos_urls)
    n_urls  = len(counts)
    visits  = list(counts.values())

    mn   = min(visits)
    mx   = max(visits)
    mean = sum(visits) / len(visits)
    med  = statistics.median(visits)

    single   = sum(1 for v in visits if v < 2)
    pct_zero = 100.0 * single / n_urls

    print(f"\n── Hard-negative validation: {npz_path} ──")
    print(f"  URLs in dataset      : {n_urls}")
    print(f"  Visits per URL       : min={mn}  max={mx}  mean={mean:.1f}  median={med:.1f}")
    print(f"  Single-visit URLs    : {single} / {n_urls}  ({pct_zero:.1f}%)")

    if pct_zero > 5.0:
        print(f"  WARNING: {pct_zero:.1f}% of URLs have <2 visits — hard negatives will be"
              " silently replaced by soft negatives for those URLs, inflating accuracy.")
    else:
        print(f"  OK: \u22645% single-visit URLs ({pct_zero:.1f}%).")

    if mn < min_visits:
        raise ValueError(
            f"{npz_path}: {single} URL(s) have fewer than {min_visits} visit(s). "
            f"Hard negatives require \u2265{min_visits} visits per URL. "
            "Collect more visits per URL or lower MODEL[\'min_visits_per_url\']."
        )

 
class QuartetDataset(Dataset):
    """
    Loads a .npz quartet archive and generates positive + negative pairs.
 
    Args:
        npz_path:      path to .npz produced by dataset_builder.py (or a test fixture)
        neg_pos_ratio: number of negative pairs per positive (default 10)
        hard_neg_frac: fraction of negatives drawn from same URL (default 0.5)
        split:         'train', 'val', or 'test'
        seed:          random seed for reproducibility
    """
 
    def __init__(self,
                 npz_path:      str,
                 neg_pos_ratio: int   = 10,
                 hard_neg_frac: float = 0.5,
                 split:         str   = "train",
                 seed:          int   = 42):
 
        data = np.load(npz_path, allow_pickle=True)
 
        # ── Load the four stream arrays (shared by both formats) ───────────
        self._ingress_up   = data["X_ingress_up"]    # (N, n_windows, L)
        self._ingress_down = data["X_ingress_down"]
        self._egress_up    = data["X_egress_up"]
        self._egress_down  = data["X_egress_down"]
        N = len(self._ingress_up)
 
        # ── Detect format and build list of (ingress_idx, egress_idx) ──────
        if "pairs" in data:
            # Full format from dataset_builder.py.
            # Ingress/egress arrays are independently shuffled; pairs records
            # which ingress index corresponds to which egress index.
            pairs_arr = data["pairs"]                        # (N, 3)
            all_pos   = [(int(r[0]), int(r[1])) for r in pairs_arr]
            flow_urls = list(data["ingress_urls"])           # indexed by ingress row
        else:
            # Simplified / test-fixture format: implicit row alignment.
            all_pos   = [(i, i) for i in range(N)]
            flow_urls = list(data["urls"])
 
        # ── URL-based train / val / test split ─────────────────────────────
        # Sort URLs alphabetically for a stable, reproducible ordering.
        unique_urls = sorted(set(flow_urls))
        U = len(unique_urls)
 
        n_train = int(U * EVAL["train_split"])
        n_val   = int(U * EVAL["val_split"])
 
        # Guarantee at least 1 URL per split when the corpus is large enough.
        if U >= 3:
            n_val   = max(1, n_val)
            n_test_ = U - n_train - n_val
            if n_test_ < 1:
                n_train -= 1
        n_test = U - n_train - n_val  # noqa: F841  (used implicitly via slice)
 
        train_urls = set(unique_urls[:n_train])
        val_urls   = set(unique_urls[n_train : n_train + n_val])
        test_urls  = set(unique_urls[n_train + n_val :])
 
        split_url_set = {"train": train_urls, "val": val_urls, "test": test_urls}[split]
 
        # Filter positive pairs to this split (by ingress URL).
        split_pos = [
            (ing, eg)
            for ing, eg in all_pos
            if flow_urls[ing] in split_url_set
        ]
 
        # positive_indices: ingress indices owned by this split.
        # Used by tests to verify disjointness and full coverage.
        self.positive_indices = [ing for ing, _ in split_pos]
 
        # ── Build URL → egress-index map for negative sampling ─────────────
        url_to_eg: dict[str, list[int]] = {}
        for ing, eg in split_pos:
            url_to_eg.setdefault(flow_urls[ing], []).append(eg)
 
        all_split_eg = [eg for _, eg in split_pos]   # all egress idx in split
 
        # ── Generate all (ingress_idx, egress_idx, label) pairs ────────────
        n_hard = int(neg_pos_ratio * hard_neg_frac)
        n_soft = neg_pos_ratio - n_hard   # total negatives = neg_pos_ratio exactly
 
        rng = np.random.default_rng(seed)
        self._pairs: list[tuple[int, int, int]] = []
 
        for ing_idx, eg_idx in split_pos:
            # Positive pair
            self._pairs.append((ing_idx, eg_idx, 1))
 
            url = flow_urls[ing_idx]
 
            # Hard negatives: same URL, different egress
            same_url_eg   = [e for e in url_to_eg.get(url, []) if e != eg_idx]
            n_hard_actual = min(n_hard, len(same_url_eg))
            if n_hard_actual > 0:
                sampled = rng.choice(
                    same_url_eg,
                    size=n_hard_actual,
                    replace=len(same_url_eg) < n_hard_actual,
                )
                for e in sampled:
                    self._pairs.append((ing_idx, int(e), 0))
 
            # Soft negatives: any egress in split except the paired one.
            # Also absorbs any shortfall from hard negatives (e.g. single-flow URLs).
            n_soft_needed = n_soft + (n_hard - n_hard_actual)
            if n_soft_needed > 0:
                other_eg = [e for e in all_split_eg if e != eg_idx]
                if other_eg:
                    sampled = rng.choice(
                        other_eg,
                        size=n_soft_needed,
                        replace=len(other_eg) < n_soft_needed,
                    )
                    for e in sampled:
                        self._pairs.append((ing_idx, int(e), 0))

        # Ingress URL for each pair — used by train.py for URL-limited subsets.
        self.pair_urls: list[str] = [flow_urls[ing] for ing, _, _ in self._pairs]
 
    # ── Dataset interface ──────────────────────────────────────────────────
 
    def __len__(self) -> int:
        return len(self._pairs)
 
    def __getitem__(self, idx: int) -> dict:
        """
        Returns dict with keys:
            ingress_up, ingress_down, egress_up, egress_down
                torch.float32 tensors of shape (n_windows, window_len)
            label
                torch.float32 scalar: 0 or 1
        """
        ing_idx, eg_idx, label = self._pairs[idx]
        return {
            "ingress_up":   torch.from_numpy(self._ingress_up[ing_idx].copy()),
            "ingress_down": torch.from_numpy(self._ingress_down[ing_idx].copy()),
            "egress_up":    torch.from_numpy(self._egress_up[eg_idx].copy()),
            "egress_down":  torch.from_numpy(self._egress_down[eg_idx].copy()),
            "label":        torch.tensor(float(label), dtype=torch.float32),
        }