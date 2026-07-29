"""
config/kde_params.py
=====================
KDE preprocessing parameters (offline dataset-building only).

Split out of config/hyperparams.py so KDE tuning changes can be synced to
leroy independently of VISIT_TIMEOUTS, which collector/coordinator.py
imports live during collection — editing that shared file while a campaign
round is running risks a torn read on the coordinator's next import (the
same class of failure that caused the 2026-07-01/2026-07-08 run_stage.sh
incidents). Nothing under collector/ or scripts/run_*.sh imports from this
file; only preprocessing/*.py and offline analysis scripts do.
"""

KDE = {
    "sigma":       0.125,   # Gaussian kernel width (seconds)
    "t_sample":    0.1,     # Sampling period (seconds) → 10 samples/sec
    "window_len":  30,      # Window length in samples (= 3 seconds)
    "overlap":     0.5,     # Fractional overlap between windows
    "min_packets": 50,      # Discard flows with fewer per-stream packets than this
                            # (raised from 5: tor .zip junk flows had <50 total ingress;
                            # per-mode override in KDE_PER_MODE for nym2 which has
                            # legitimately thin upstream traffic)
}

# Per-mode KDE overrides — duration and sigma vary with anonymity system latency
#
# duration was originally a flat 30s/60s guess per mode, unvalidated against real
# capture spans. Measured directly from round_01 pcaps (2026-07-09, n=300 sampled
# per mode, ingress+egress separately) via extract_packets() timestamp spans:
#   vpn:  ingress median=0.16s p95=0.27s p99=0.30s max=0.32s
#         egress  median=0.19s p95=2.70s p99=3.15s max=3.29s
#   tor:  ingress median=10.70s p95=85.69s p99=297.20s max=302.81s
#         egress  median=12.04s p95=86.04s p99=299.92s max=304.96s
#   nym5: ingress median=15.08s p95=21.45s p99=28.40s max=140.42s
#         egress  median=7.87s  p95=15.41s p99=20.97s max=135.95s
# The old 30s/60s durations were wildly oversized relative to actual traffic
# (vpn especially: real visits complete in <3.3s over a direct WireGuard tunnel
# with no relay latency, so a 30s window left ~18 of 19 windows pure padding —
# confirmed via kde_shape_check.py: vpn nonzero_frac_global=0.055, 100% of flows
# flagged mostly-empty; tor 0.295 (DEGENERATE, just under the 0.3 threshold);
# nym5 0.302 (technically passed but 58% of individual flows were still
# mostly-empty — a fragile pass, not a real one).
# New values are sized to the TYPICAL case (biased toward median/p95, not
# p99/max) for all three — tor and nym5 both have a heavy right tail (slow
# circuits / mixnet hops) that would force median-case windows back into
# padding-dominated territory if sized to cover it. kde_shape() already crops
# anything beyond duration (see preprocessing/kde.py), so this is a tightening
# of an existing truncation behavior, not new lossy behavior — flows longer
# than duration lose only their tail beyond it, same as before.
KDE_PER_MODE = {
    # tor: median span ~11s; originally sized to ~2x median (24s) while
    # accepting truncation of the long tail (p95=86s, p99=298s) — covering
    # the tail would recreate the same padding-dominated windows for the
    # 80%+ of flows that complete quickly.
    # RETUNED 2026-07-12 (24.0 -> 32.0): live-campaign audit found real tor
    # visits reaching 37-38s against the 24s window -- ~10% of sampled
    # visits truncated at the tail (see patches/00_pause_batch_manifest.md
    # / audit findings). 32s covers this without reopening the padding
    # problem the original retune fixed -- still far short of the true
    # p95=86s long tail, so this is a modest widening within the existing
    # truncation tradeoff, not a reversal of it. n_windows recalculates
    # automatically from duration/t_sample/overlap; not hand-counted here.
    # window_len RETUNED 2026-07-29 (30 -> 10, i.e. 1s not 3s per window):
    # per-mode window_len pilot sweep (5 candidates x 1 seed, real round_01
    # campaign data, validation PR-AUC) found wl=10 clearly best for tor
    # (0.8469 vs 0.7980-0.8351 for the other 4 candidates) -- window_len
    # was previously a single global constant (KDE["window_len"]=30) never
    # tuned per mode, unlike duration/sigma. See thesis Section~sec:setup
    # for the full sweep methodology and results table.
    "tor":      {"duration": 32.0,  "sigma": 0.25, "window_len": 10},
    # vpn: real visits are near-instantaneous (no relay latency) — egress
    # (the longer of the two streams) has p99=3.15s, max=3.29s. 6s duration
    # covers max with ~1.8x margin. n_windows=3.
    # window_len pilot sweep (2026-07-29): all 5 candidates scored within
    # 0.5% of each other (0.9944-0.9997) -- no real signal, unlike the
    # other three modes. Left at the global default (30) rather than
    # changed on a within-noise result.
    "vpn":      {"duration": 6.0,   "sigma": 0.125},
    # nym5: sized to ingress p95=21.45s (the larger of ingress/egress) with a
    # buffer, landing at 30s — same window count as nym2 coincidentally.
    # Accepts truncation of the rare >30s outlier (max=140s) for the same
    # reason as tor above. n_windows=19.
    # window_len pilot sweep (2026-07-29): wl=40 scored best (0.3889), but
    # against the actual prior default (wl=30, 0.3875) that's only +0.4%
    # relative -- within single-seed pilot noise, not a real signal. (An
    # earlier draft of this note compared wl=40 to the worst candidate,
    # wl=15 at 0.2305, and reported that gap, ~69% relative, as if it were
    # the improvement over the prior config -- it wasn't; corrected here.)
    # Left at the global default (30), same reasoning as vpn.
    "nym5":     {"duration": 30.0,  "sigma": 0.5},
    # nym2 (2-hop WireGuard): confirmed on pilot (56/59 visits built, span p95=9.2s, max=9.8s).
    # duration=30s: p95_span(9.2s)+10s buffer → 30s floor. n_windows=19 (same as vpn).
    # sigma=0.2: WireGuard UDP is high-density (~16k pkts/6s); inter-packet gap (~0.001s) is
    # meaningless for kernel width. 0.2s smooths the burst envelope without collapsing structure.
    # min_packets=5: nym2's upstream (client→mix) has very few packets per visit; the global 50
    # would drop essentially all nym2 flows. Keep permissive here; yield gate catches real loss.
    # window_len RETUNED 2026-07-29 (30 -> 10, i.e. 1s not 3s per window):
    # window_len pilot sweep found wl=10 best (0.8252); the previous global
    # default (30) scored worst of nym2's 5 candidates (0.7995).
    "nym2":     {"duration": 30.0,  "sigma": 0.2, "min_packets": 5, "window_len": 10},
}
