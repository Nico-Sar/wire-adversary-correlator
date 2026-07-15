#!/usr/bin/env bash
# scripts/staged_collect.sh
# =========================
# Staged full-dataset collection (all 4 modes) for leroy.
#
# Default mode: KEEP_ONLY=1 (archives stay on leroy scratch permanently).
# Per stage:
#   1. Run all 4 modes fully concurrently — vpn (vpn-client1 + vpn-client2),
#      tor (tor-client1 + tor-client2), nym5 (nym5-client1 + nym5-client2),
#      nym2 (nym2-client1 + nym2-client2). Each mode has its own egress port
#      (8080/8081/8082/80), so there are no BPF capture collisions between
#      modes — full concurrency does not require staging groups.
#   2. Compress raw output → tar.zst
#   3. Verify archive integrity (zstd -t + tar -tf)
#   4. Delete the raw stage dir (archive is kept permanently)
#   5. Record DONE in manifest
# Re-running resumes from the first incomplete stage.
#
# Move archives to NAS later (from Lex) with scripts/pull_and_archive.sh.
#
# ── Launch (inside tmux on leroy) ─────────────────────────────────────────────
#   tmux new -s collect
#   cd /volume1/scratch/r1086364/wire-adversary-correlator
#   source .venv/bin/activate
#   CONFIG_LABEL=full500_v1 \
#   VISITS=50 \
#   URLS_PER_STAGE=25 \
#   DATA_ROOT=/volume1/scratch/r1086364/staged_data \
#   bash scripts/staged_collect.sh 2>&1 | tee /volume1/scratch/r1086364/staged_collect.log
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Configuration (override via env vars) ─────────────────────────────────────
REPO_ROOT="${REPO_ROOT:-/volume1/scratch/r1086364/wire-adversary-correlator}"
URLS_FILE="${URLS_FILE:-$REPO_ROOT/config/urls.txt}"
URLS_FILE_NYM2="${URLS_FILE_NYM2:-$REPO_ROOT/config/urls_nym2.txt}"
URLS_PER_STAGE="${URLS_PER_STAGE:-25}"
VISITS="${VISITS:-50}"              # visits per client per URL; 2 nym clients → 100 data points/URL
DATA_ROOT="${DATA_ROOT:-/volume1/scratch/r1086364/staged_data}"
MIN_FREE_GB="${MIN_FREE_GB:-30}"    # abort if less than this is free on DATA_ROOT
CONFIG_LABEL="${CONFIG_LABEL:-full500}"

# KEEP_ONLY=1 (default): keep compressed archives on leroy, skip all remote ship/verify steps.
# Set KEEP_ONLY=0 only if you want the old PUSH-to-Lex-during-run behaviour (requires REMOTE_SSH).
KEEP_ONLY="${KEEP_ONLY:-1}"
ESTIMATED_STAGE_GB="${ESTIMATED_STAGE_GB:-10}"  # conservative per-stage archive estimate for capacity check

# Only needed when KEEP_ONLY=0 (remote PUSH mode):
REMOTE_SSH="${REMOTE_SSH:-}"
REMOTE_PATH="${REMOTE_PATH:-/home/nico/Desktop/Masters_Thesis/wire-adversary-correlator/data/raw/staged}"

# ── Internal paths ────────────────────────────────────────────────────────────
MANIFEST="$DATA_ROOT/manifest_${CONFIG_LABEL}.txt"
LOCK_FILE="/tmp/staged_collect_${CONFIG_LABEL}.lock"
SLICE_DIR="$DATA_ROOT/_url_slices_${CONFIG_LABEL}"
RAW_BASE="$DATA_ROOT/${CONFIG_LABEL}"
ARCHIVE_DIR="$DATA_ROOT/archives_${CONFIG_LABEL}"

# ── Logging ───────────────────────────────────────────────────────────────────
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
die() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] $*" >&2; exit 1; }

# ── Kill background jobs on any exit (failure or SIGTERM/SIGINT) ──────────────
_cleanup_bg() {
    local pids
    pids=$(jobs -p 2>/dev/null) || true
    if [[ -n "$pids" ]]; then
        log "Cleanup: killing background jobs ($pids)"
        kill $pids 2>/dev/null || true
        wait 2>/dev/null || true
    fi
}
trap _cleanup_bg EXIT

# ── Disk space guard ──────────────────────────────────────────────────────────
check_disk() {
    local avail_gb
    avail_gb=$(df -BG "$DATA_ROOT" | awk 'NR==2 {gsub("G",""); print $4}')
    if (( avail_gb < MIN_FREE_GB )); then
        die "Low disk: ${avail_gb}G free on $DATA_ROOT, need ${MIN_FREE_GB}G. Aborting."
    fi
    log "Disk: ${avail_gb}G free (threshold: ${MIN_FREE_GB}G)."
}

# ── Manifest helpers ──────────────────────────────────────────────────────────
stage_is_done() {
    # Match prefix only: manifest lines are "DONE stage_N <timestamp>"
    # -x would require a full-line match and would never fire due to the timestamp.
    grep -qF "DONE stage_$1 " "$MANIFEST" 2>/dev/null
}

mark_done() {
    echo "DONE stage_$1 $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$MANIFEST"
}

# ── Pre-flight checks ─────────────────────────────────────────────────────────
preflight() {
    log "=== Pre-flight checks ==="

    [[ -d "$REPO_ROOT" ]] || die "REPO_ROOT not found: $REPO_ROOT"
    [[ -f "$URLS_FILE" ]] || die "URLS_FILE not found: $URLS_FILE"
    [[ -f "$URLS_FILE_NYM2" ]] || die "URLS_FILE_NYM2 not found: $URLS_FILE_NYM2"

    cd "$REPO_ROOT"
    # coordinator.py runs argparse at module level so `import` always exits non-zero;
    # --help is the lightest invocation that actually proves the module is loadable.
    python3 -m collector.coordinator --help >/dev/null 2>&1 \
        || die "collector.coordinator not runnable. Activate venv or check PYTHONPATH."

    local required_tools=(zstd tar flock sha256sum)
    if [[ "$KEEP_ONLY" != "1" ]]; then
        required_tools+=(rsync ssh)
    fi
    for tool in "${required_tools[@]}"; do
        command -v "$tool" >/dev/null || die "Required tool not found: $tool"
    done

    if [[ "$KEEP_ONLY" != "1" ]]; then
        [[ -n "$REMOTE_SSH" ]] \
            || die "REMOTE_SSH is not set and KEEP_ONLY != 1. Example: REMOTE_SSH=nico@<ip>"

        log "Testing SSH to Lex ($REMOTE_SSH) ..."
        ssh -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE_SSH" true \
            || die "Cannot reach $REMOTE_SSH without a password prompt. Set up key-based auth:
  On leroy: ssh-copy-id $REMOTE_SSH"
        log "SSH to Lex: OK"

        ssh -o BatchMode=yes "$REMOTE_SSH" "mkdir -p '$REMOTE_PATH'" \
            || die "Cannot create $REMOTE_PATH on $REMOTE_SSH"
        log "Remote path ready: ${REMOTE_SSH}:${REMOTE_PATH}"
    else
        log "KEEP_ONLY=1: archives kept on leroy — no remote ship configured."
    fi

    mkdir -p "$DATA_ROOT" "$SLICE_DIR" "$RAW_BASE" "$ARCHIVE_DIR"
    touch "$MANIFEST"

    log "=== Pre-flight OK ==="
}

# ── Capacity check: abort early if projected dataset won't fit ────────────────
capacity_check() {
    log "=== Capacity check ==="

    # Free space on DATA_ROOT
    local avail_gb
    avail_gb=$(df -BG "$DATA_ROOT" | awk 'NR==2 {gsub("G",""); print $4}')
    log "Free on $DATA_ROOT: ${avail_gb} GiB"

    # ESAT scratch quota (best-effort; quota may not be installed)
    if command -v quota >/dev/null 2>&1; then
        log "quota -s output:"
        quota -s 2>/dev/null | sed 's/^/  /' || true
    else
        log "(quota tool not found — manual check: df -h $DATA_ROOT)"
    fi

    # Motd may contain purge / inactivity policy warnings
    for f in /etc/motd /run/motd /run/motd.d/*; do
        [[ -f "$f" ]] || continue
        if grep -qi -E "purge|inactiv|scratch|quota|expir" "$f" 2>/dev/null; then
            log "NOTICE from $f:"
            grep -i -E "purge|inactiv|scratch|quota|expir" "$f" | sed 's/^/  /' || true
        fi
    done

    # Estimate per-stage size from any existing archive, else use ESTIMATED_STAGE_GB
    local stage_gb="$ESTIMATED_STAGE_GB"
    local existing_archive
    existing_archive=$(find "$ARCHIVE_DIR" -name "*.tar.zst" -printf '%f\n' 2>/dev/null | head -1)
    if [[ -n "$existing_archive" ]]; then
        local real_gb
        real_gb=$(du -BG "$ARCHIVE_DIR/$existing_archive" 2>/dev/null | awk '{gsub("G",""); print $1}')
        if [[ -n "$real_gb" && "$real_gb" -gt 0 ]]; then
            stage_gb="$real_gb"
            log "Stage size from existing archive $existing_archive: ${stage_gb} GiB"
        fi
    else
        log "No existing archives — using ESTIMATED_STAGE_GB=${stage_gb} GiB per stage."
    fi

    # Count remaining (not yet DONE) stages
    local n_remaining=$(( N_STAGES - $(grep -c "^DONE " "$MANIFEST" 2>/dev/null || echo 0) ))
    local projected_gb=$(( stage_gb * n_remaining ))
    local needed_gb=$(( projected_gb + MIN_FREE_GB ))

    log "Remaining stages: $n_remaining  |  Projected: ~${projected_gb} GiB  |  Need (incl. ${MIN_FREE_GB} GiB margin): ${needed_gb} GiB"

    if (( avail_gb < needed_gb )); then
        die "Insufficient disk space: ${avail_gb} GiB free, need ${needed_gb} GiB (${projected_gb} GiB data + ${MIN_FREE_GB} GiB margin).
  Free up space or reduce stages. Run 'du -sh $ARCHIVE_DIR' to see existing archives."
    fi

    log "Capacity OK: ${avail_gb} GiB free ≥ ${needed_gb} GiB required."
    log "=== Capacity check passed ==="
}

# ── Build URL slice files (idempotent) ────────────────────────────────────────
#   Strips comment lines and blanks from URLS_FILE, then chops into per-stage
#   files under SLICE_DIR. Called once before the stage loop.
build_slices() {
    # Main URL list (vpn / tor / nym5)
    local valid_urls="$SLICE_DIR/_all_valid.txt"
    grep -v '^[[:space:]]*#' "$URLS_FILE" \
        | grep -v '^[[:space:]]*$' \
        > "$valid_urls"
    TOTAL_URLS=$(wc -l < "$valid_urls")
    N_STAGES=$(( (TOTAL_URLS + URLS_PER_STAGE - 1) / URLS_PER_STAGE ))

    log "Total valid URLs: $TOTAL_URLS | URLS/stage: $URLS_PER_STAGE | Stages: $N_STAGES | Visits/client: $VISITS"

    local stage start
    for stage in $(seq 1 "$N_STAGES"); do
        local slice="$SLICE_DIR/stage_$(printf '%03d' "$stage").txt"
        [[ -f "$slice" ]] && continue
        start=$(( (stage - 1) * URLS_PER_STAGE + 1 ))
        sed -n "${start},$((start + URLS_PER_STAGE - 1))p" "$valid_urls" > "$slice"
    done

    # nym2 URL list (audio/video stripped to avoid 360s WireGuard timeouts)
    local valid_urls_nym2="$SLICE_DIR/_all_valid_nym2.txt"
    grep -v '^[[:space:]]*#' "$URLS_FILE_NYM2" \
        | grep -v '^[[:space:]]*$' \
        > "$valid_urls_nym2"
    local total_nym2
    total_nym2=$(wc -l < "$valid_urls_nym2")
    N_STAGES_NYM2=$(( (total_nym2 + URLS_PER_STAGE - 1) / URLS_PER_STAGE ))

    log "nym2 URLs: $total_nym2 | Stages: $N_STAGES_NYM2 (from $URLS_FILE_NYM2)"

    for stage in $(seq 1 "$N_STAGES_NYM2"); do
        local nym2_slice="$SLICE_DIR/nym2_stage_$(printf '%03d' "$stage").txt"
        [[ -f "$nym2_slice" ]] && continue
        start=$(( (stage - 1) * URLS_PER_STAGE + 1 ))
        sed -n "${start},$((start + URLS_PER_STAGE - 1))p" "$valid_urls_nym2" > "$nym2_slice"
    done
}

# ── Single stage: collect → compress → ship → verify → clean ─────────────────
run_stage() {
    local stage="$1"
    local tag
    tag=$(printf '%03d' "$stage")
    local slice="$SLICE_DIR/stage_${tag}.txt"
    local stage_dir="$RAW_BASE/stage_${tag}"
    local archive="$ARCHIVE_DIR/${CONFIG_LABEL}_stage${tag}.tar.zst"
    local n_urls
    n_urls=$(wc -l < "$slice")

    log "══════════════════════════════════════════"
    log "Stage $stage / $N_STAGES  ($n_urls URLs  ×  $VISITS visits/client)"
    log "══════════════════════════════════════════"

    check_disk

    # Wipe any partial previous attempt so the coordinator starts clean
    rm -rf "$stage_dir"
    mkdir -p "$stage_dir"

    # ── All 4 modes, both clients each, fully concurrent ──────────────────────
    # Distinct egress ports per mode (vpn=8080, tor=8081, nym5=8082, nym2=80)
    # mean no BPF capture collisions — no staging groups needed.
    log "[stage $stage] Concurrent start: vpn + tor + nym5 + nym2 (2 clients each) ..."

    python3 -m collector.coordinator \
        --mode vpn --urls "$slice" --visits "$VISITS" \
        --output "$stage_dir" --client vpn-client1 &
    local P_VP1=$!

    python3 -m collector.coordinator \
        --mode vpn --urls "$slice" --visits "$VISITS" \
        --output "$stage_dir" --client vpn-client2 &
    local P_VP2=$!

    python3 -m collector.coordinator \
        --mode tor --urls "$slice" --visits "$VISITS" \
        --output "$stage_dir" --client tor-client1 --rotate-circuits &
    local P_T1=$!

    python3 -m collector.coordinator \
        --mode tor --urls "$slice" --visits "$VISITS" \
        --output "$stage_dir" --client tor-client2 --rotate-circuits &
    local P_T2=$!

    python3 -m collector.coordinator \
        --mode nym5 --urls "$slice" --visits "$VISITS" \
        --output "$stage_dir" --client nym5-client1 --rotate-circuits &
    local P_N5_1=$!

    python3 -m collector.coordinator \
        --mode nym5 --urls "$slice" --visits "$VISITS" \
        --output "$stage_dir" --client nym5-client2 --rotate-circuits &
    local P_N5_2=$!

    # nym2 uses a separate URL list (urls_nym2.txt) that omits large audio/video
    # files to avoid 360s WireGuard timeout failures.  It has fewer stages than
    # the main modes; skip gracefully once its stages are exhausted.
    local nym2_slice="$SLICE_DIR/nym2_stage_${tag}.txt"
    local P_N2_1="" P_N2_2=""
    if [[ ! -f "$nym2_slice" ]]; then
        log "[stage $stage] nym2: no slice file (stage > $N_STAGES_NYM2) — skipping nym2."
    else
        python3 -m collector.coordinator \
            --mode nym2 --urls "$nym2_slice" --visits "$VISITS" \
            --output "$stage_dir" --client nym2-client1 --rotate-circuits &
        P_N2_1=$!

        python3 -m collector.coordinator \
            --mode nym2 --urls "$nym2_slice" --visits "$VISITS" \
            --output "$stage_dir" --client nym2-client2 --rotate-circuits &
        P_N2_2=$!
    fi

    wait $P_VP1 || die "vpn-client1 failed at stage $stage — raw data left at $stage_dir"
    wait $P_VP2 || die "vpn-client2 failed at stage $stage — raw data left at $stage_dir"
    wait $P_T1  || die "tor-client1 failed at stage $stage — raw data left at $stage_dir"
    wait $P_T2  || die "tor-client2 failed at stage $stage — raw data left at $stage_dir"
    wait $P_N5_1 || die "nym5-client1 failed at stage $stage — raw data left at $stage_dir"
    wait $P_N5_2 || die "nym5-client2 failed at stage $stage — raw data left at $stage_dir"
    [[ -n "$P_N2_1" ]] && { wait $P_N2_1 || die "nym2-client1 failed at stage $stage — raw data left at $stage_dir"; }
    [[ -n "$P_N2_2" ]] && { wait $P_N2_2 || die "nym2-client2 failed at stage $stage — raw data left at $stage_dir"; }
    log "[stage $stage] Concurrent group done."

    # ── Compress ──────────────────────────────────────────────────────────────
    log "[stage $stage] Compressing → $(basename "$archive") ..."
    # tar -cf - streams the stage dir; zstd -T0 uses all cores; -6 is the default level.
    tar -cf - -C "$RAW_BASE" "stage_${tag}" | zstd -T0 -6 -o "$archive"
    log "[stage $stage] Compressed: $(du -sh "$archive" | cut -f1)"

    # ── Integrity gate (MUST pass before any deletion) ────────────────────────
    log "[stage $stage] Verifying archive integrity ..."
    zstd -t "$archive"                          # test zstd frame
    zstd -dc "$archive" | tar -tf - > /dev/null # test tar structure end-to-end
    log "[stage $stage] Archive integrity OK."

    if [[ "$KEEP_ONLY" == "1" ]]; then
        # ── KEEP_ONLY: delete raw dir, keep archive permanently ───────────────
        rm -rf "$stage_dir"
        log "[stage $stage] Raw stage dir deleted. Archive kept: $archive"
        log "[stage $stage] Archive size: $(du -sh "$archive" | cut -f1)"
    else
        # ── PUSH mode: ship → remote verify → delete everything ───────────────
        # Generate sha256 with only the basename so sha256sum --check works on Lex
        ( cd "$ARCHIVE_DIR" && sha256sum "$(basename "$archive")" ) > "${archive}.sha256"
        log "[stage $stage] Shipping to ${REMOTE_SSH}:${REMOTE_PATH}/ ..."
        rsync -az --progress "$archive" "${archive}.sha256" \
            "${REMOTE_SSH}:${REMOTE_PATH}/"

        log "[stage $stage] Verifying remote checksum ..."
        local sha_base
        sha_base=$(basename "${archive}.sha256")
        ssh -o BatchMode=yes "$REMOTE_SSH" \
            "cd '${REMOTE_PATH}' && sha256sum --check '${sha_base}'" \
            || die "Remote checksum FAILED at stage $stage. Raw data preserved at $stage_dir. Archive left at $archive."
        log "[stage $stage] Remote checksum OK."

        rm -rf "$stage_dir"
        rm -f "$archive" "${archive}.sha256"
        log "[stage $stage] Local raw data and archive deleted (verified remote copy)."
    fi

    # ── Mark done ─────────────────────────────────────────────────────────────
    mark_done "$stage"
    log "[stage $stage] ══ DONE ✓ ══"
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
    log "staged_collect.sh starting  (CONFIG_LABEL=$CONFIG_LABEL  KEEP_ONLY=$KEEP_ONLY)"

    preflight

    # Acquire exclusive lock — prevents two sessions from running simultaneously
    exec 200>"$LOCK_FILE"
    flock -n 200 \
        || die "Another instance is already running (lock: $LOCK_FILE). Exiting."

    build_slices
    capacity_check

    # Rough wall-time estimate per stage — all 4 modes run fully concurrently,
    # so the stage wall time is the SLOWEST mode, not the sum:
    #   vpn/tor (2 clients):  URLS × VISITS × 6s  / 60 / 2
    #   nym5    (2 clients):  URLS × VISITS × 30s / 60 / 2
    #   nym2    (2 clients):  URLS × VISITS × 34s / 60 / 2
    #   nym2 uses urls_nym2.txt (100 URLs, $N_STAGES_NYM2 stages) — no large
    #   audio/video files; stages $((N_STAGES_NYM2 + 1))–$N_STAGES skip nym2.
    local g1 g2 g3 stage_est est_h
    g1=$(( URLS_PER_STAGE * VISITS * 6  / 60 / 2 ))
    g2=$(( URLS_PER_STAGE * VISITS * 30 / 60 / 2 ))
    g3=$(( URLS_PER_STAGE * VISITS * 34 / 60 / 2 ))
    stage_est=$g1
    (( g2 > stage_est )) && stage_est=$g2
    (( g3 > stage_est )) && stage_est=$g3
    est_h=$(( stage_est / 60 ))
    log "Estimated wall time per stage: ~${stage_est} min (~${est_h}h)"
    log "Total estimate for $N_STAGES stages: ~$(( stage_est * N_STAGES / 60 ))h"

    local completed=0
    for stage in $(seq 1 "$N_STAGES"); do
        if stage_is_done "$stage"; then
            log "Stage $stage: already done (manifest), skipping."
            completed=$(( completed + 1 ))
            continue
        fi
        run_stage "$stage"
        completed=$(( completed + 1 ))
    done

    log "════════════════════════════════════════════"
    log "All $N_STAGES stages complete. Collection finished."
    if [[ "$KEEP_ONLY" == "1" ]]; then
        log "Archives on leroy: $ARCHIVE_DIR"
        log "Move to NAS from Lex: bash scripts/pull_and_archive.sh"
    else
        log "Archives on Lex: ${REMOTE_SSH}:${REMOTE_PATH}/"
    fi
    log "Manifest: $MANIFEST"
    log "════════════════════════════════════════════"
}

main "$@"
