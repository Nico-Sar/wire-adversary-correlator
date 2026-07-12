#!/usr/bin/env bash
# scripts/transition_nym5_to_split.sh
# =====================================
# ONE-TIME, MANUAL-TRIGGER transition: closes the shared round_03
# transitional window (see patches/10_nym5_instance_separation_design.md)
# and launches nym5's own split instance (data/campaign_nym5, starting at
# round_04). Run this once nym5-client2 has finished its round_03 backlog
# — NOT automatically triggered, NOT run by any cron/watcher.
#
# SAFE TO RUN EARLY: if client2 is still working or still short of its
# round_03 quota, this refuses cleanly (exit 1) and does nothing — no
# partial state, no forced audit, no launch.
# SAFE TO RUN TWICE: if the nym5 split instance is already running against
# data/campaign_nym5, this detects that and skips re-launching. If
# round_03 already has .audit_passed, it skips straight to the launch
# check instead of re-running the audit.
#
# Usage (from repo root):
#   bash scripts/transition_nym5_to_split.sh
#
# Exit codes: 0 = transitioned (or already transitioned, idempotent no-op).
#             1 = not ready yet, or audit failed — nothing was changed.

set -uo pipefail   # no -e: we want to control every failure path explicitly

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

ROUND03_DIR="data/campaign/round_03"
NYM5_JSONL="$ROUND03_DIR/nym5_visits.jsonl"
LIGHT_STAGE_FILE="data/campaign/_url_slices/light/stage_03.txt"
VALIDATED_FULL="data/campaign/stage0/validated_urls.txt"
VALIDATED_LIGHT="data/campaign/stage0/validated_urls_light.txt"
NYM5_CAMPAIGN_ROOT="data/campaign_nym5"
LICENSE_DEADLINE="2026-07-19"
VISITS_LIGHT_TARGET=48
COORDINATOR_PYTHON="$REPO_ROOT/.venv/bin/python3"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [transition] $*"; }

log "=========================================================="
log "nym5 round_03 -> split-instance transition"
log "=========================================================="

# ── Step 1: GUARD — this is the whole safety of the script ─────────────────
log "--- Step 1: GUARD (process + quota check) ---"

RUNNING_PIDS=$(pgrep -f "collector\.coordinator --mode nym5 .*--output $ROUND03_DIR" || true)
if [[ -n "$RUNNING_PIDS" ]]; then
    log "[REFUSE] nym5 coordinator process(es) still running against $ROUND03_DIR (PID(s): $RUNNING_PIDS)."
    log "[REFUSE] client2 (and/or client1) is still actively collecting round_03 — not safe to transition."
    log "[REFUSE] Nothing was changed. Re-run this script once the process(es) above have exited."
    exit 1
fi
log "[ok] no nym5 coordinator process running against $ROUND03_DIR"

if [[ ! -f "$NYM5_JSONL" ]]; then
    log "[REFUSE] $NYM5_JSONL not found — cannot verify quota. Nothing was changed."
    exit 1
fi
if [[ ! -f "$LIGHT_STAGE_FILE" ]]; then
    log "[REFUSE] $LIGHT_STAGE_FILE not found — cannot verify quota. Nothing was changed."
    exit 1
fi

log "Checking per-client round_03 quota (target ${VISITS_LIGHT_TARGET}/URL/client)..."
QUOTA_CHECK=$("$COORDINATOR_PYTHON" - "$LIGHT_STAGE_FILE" "$NYM5_JSONL" "$VISITS_LIGHT_TARGET" <<'PYEOF'
import json, sys
from collections import defaultdict

sys.path.insert(0, ".")
from config.infrastructure import URL_BASE

light_stage_file, jsonl_path, target = sys.argv[1], sys.argv[2], int(sys.argv[3])

url_base = URL_BASE["nym5"]
urls = [url_base + "/" + l.strip() for l in open(light_stage_file) if l.strip() and not l.startswith("#")]

per_client = {"nym5-client1": defaultdict(int), "nym5-client2": defaultdict(int)}
for line in open(jsonl_path):
    line = line.strip()
    if not line:
        continue
    try:
        r = json.loads(line)
    except Exception:
        continue
    if r.get("visit_status") != "success":
        continue
    vid = r.get("visit_id", "")
    for cid in per_client:
        if vid.startswith(cid + "_"):
            per_client[cid][r.get("url", "")] += 1

overall_ready = True
for cid, counts in per_client.items():
    short = [(u, target - counts.get(u, 0)) for u in urls if counts.get(u, 0) < target]
    if short:
        overall_ready = False
        total_gap = sum(d for _, d in short)
        print(f"NOTREADY {cid}: {len(short)} URL(s) short of {target}, total gap {total_gap} visits")
    else:
        print(f"READY {cid}: quota complete ({len(urls)} URLs x {target})")

print("OVERALL_READY" if overall_ready else "OVERALL_NOTREADY")
PYEOF
)
echo "$QUOTA_CHECK" | sed 's/^/  /'

if echo "$QUOTA_CHECK" | grep -q "^OVERALL_NOTREADY"; then
    log "[REFUSE] at least one nym5 client still has a round_03 quota gap (see above). Nothing was changed."
    exit 1
fi
log "[ok] both nym5-client1 and nym5-client2 have completed their round_03 quota"

# ── Step 2: AUDIT — require a genuine pass, not just an exit code ──────────
log "--- Step 2: AUDIT ---"

if [[ -f "$ROUND03_DIR/.audit_passed" ]]; then
    log "[ok] $ROUND03_DIR/.audit_passed already present — skipping re-audit (idempotent: this script"
    log "     was likely already run successfully once). Proceeding to the launch check."
else
    AUDIT_OUTPUT=$(bash scripts/audit_stage.sh "$ROUND03_DIR" "data/campaign" "$LICENSE_DEADLINE" 2>&1)
    AUDIT_RC=$?
    echo "$AUDIT_OUTPUT" | sed 's/^/  /'

    if [[ $AUDIT_RC -ne 0 ]]; then
        log "[REFUSE] audit_stage.sh HALTed (exit $AUDIT_RC) — see [FLAG] line(s) above."
        log "[REFUSE] .audit_passed NOT touched. Nothing was changed. Review and re-run manually once fixed."
        exit 1
    fi

    # Belt-and-suspenders: don't just trust the exit code. audit_stage.sh's
    # RED_FLAG is a single shared variable across all 6 sections, so exit 0
    # already implies no section flagged anything -- but explicitly check
    # anyway, since this decision (marking .audit_passed, launching a new
    # instance) is consequential enough to not rely on that implicitly.
    if echo "$AUDIT_OUTPUT" | grep -q '\[FLAG\]'; then
        log "[REFUSE] audit exited 0 but [FLAG] line(s) are present in the output — inconsistent,"
        log "[REFUSE] treating as unsafe. .audit_passed NOT touched. Review manually."
        exit 1
    fi

    SEC0=$(echo "$AUDIT_OUTPUT" | sed -n '/--- 0\. Launch sanity/,/--- 1\./p')
    if ! echo "$SEC0" | grep -q '\[ok\]'; then
        log "[REFUSE] Section 0 (launch sanity) did not report [ok] — review manually. .audit_passed NOT touched."
        exit 1
    fi
    log "[ok] Section 0 (launch sanity): clean"

    SEC1=$(echo "$AUDIT_OUTPUT" | sed -n '/--- 1\. Per-mode yield/,/--- 2\./p')
    if echo "$SEC1" | grep -qiE "zero collection|zero successful|below.*threshold"; then
        log "[REFUSE] Section 1 (per-mode yield) shows a zero-collection or below-threshold mode — review manually. .audit_passed NOT touched."
        exit 1
    fi
    if ! echo "$SEC1" | grep -qi 'nym5.*\[ok\]\|nym5:.*yield'; then
        log "[REFUSE] Section 1 does not show a clean nym5 yield line — review manually. .audit_passed NOT touched."
        exit 1
    fi
    log "[ok] Section 1 (per-mode yield): clean, nym5 yield reported healthy"

    # ── Step 3: MARK — only reached on a clean audit ────────────────────────
    log "--- Step 3: MARK ---"
    touch "$ROUND03_DIR/.audit_passed"
    log "[ok] touched $ROUND03_DIR/.audit_passed"
fi

# ── Step 4: LAUNCH — idempotent, skip if already running ───────────────────
log "--- Step 4: LAUNCH nym5 split instance ---"

ALREADY_RUNNING=$(pgrep -f "collector\.coordinator --mode nym5 .*--output $NYM5_CAMPAIGN_ROOT" || true)
if [[ -n "$ALREADY_RUNNING" ]]; then
    log "[SKIP] nym5 coordinator process(es) already running against $NYM5_CAMPAIGN_ROOT (PID(s): $ALREADY_RUNNING)."
    log "[SKIP] Split instance already launched — not re-launching (idempotent). Nothing more to do."
    exit 0
fi

log "Launching: MODE_SCOPE=nym5 VISITS_LIGHT=$VISITS_LIGHT_TARGET bash scripts/run_campaign.sh $VALIDATED_FULL $VALIDATED_LIGHT $NYM5_CAMPAIGN_ROOT $LICENSE_DEADLINE 4"

tmux has-session -t campaign 2>/dev/null || tmux new-session -d -s campaign
tmux new-window -t campaign -n nym5-split 2>/dev/null || log "(tmux window campaign:nym5-split already exists, reusing it)"
tmux send-keys -t campaign:nym5-split \
    "cd $REPO_ROOT && MODE_SCOPE=nym5 VISITS_LIGHT=$VISITS_LIGHT_TARGET bash scripts/run_campaign.sh $VALIDATED_FULL $VALIDATED_LIGHT $NYM5_CAMPAIGN_ROOT $LICENSE_DEADLINE 4" Enter
log "[ok] launch command sent to tmux window campaign:nym5-split"

log "Waiting 8s, then checking what actually launched..."
sleep 8
LAUNCHED=$(pgrep -af "collector\.coordinator --mode nym5 .*--output $NYM5_CAMPAIGN_ROOT" || true)
if [[ -z "$LAUNCHED" ]]; then
    log "[WARNING] no nym5 coordinator process found against $NYM5_CAMPAIGN_ROOT yet — it may still be"
    log "[WARNING] doing preflight checks (ssh-agent, router reachability, slice regeneration)."
    log "[WARNING] Check manually: tmux attach -t campaign  (window: nym5-split)"
else
    log "[ok] confirmed running against the NEW tree ($NYM5_CAMPAIGN_ROOT):"
    echo "$LAUNCHED" | sed 's/^/  /'
    NON_NYM5=$(echo "$LAUNCHED" | grep -v -- "--client nym5-" || true)
    if [[ -n "$NON_NYM5" ]]; then
        log "[WARNING] unexpected non-nym5 client(s) launched against $NYM5_CAMPAIGN_ROOT:"
        echo "$NON_NYM5" | sed 's/^/  /'
    else
        log "[ok] only nym5-client1/nym5-client2 launched, as expected"
    fi
fi

log "=========================================================="
log "Transition complete. nym5 is now running as its own split instance"
log "in $NYM5_CAMPAIGN_ROOT, starting at round_04, fully independent of"
log "the fast instance in data/campaign_fast."
log "=========================================================="
