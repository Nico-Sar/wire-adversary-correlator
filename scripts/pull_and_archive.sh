#!/usr/bin/env bash
# scripts/pull_and_archive.sh
# ============================
# RUN ON LEX. Streams compressed stage archives from leroy to the Nextcloud NAS
# via rclone — Lex is a pure relay, the dataset never accumulates on Lex disk.
#
# Assumptions (set up ONCE before first run, not done by this script):
#
#   1. SSH alias "leroy" in ~/.ssh/config on Lex, routing through the KU jump chain:
#        Host leroy
#          HostName leroy.esat.kuleuven.be
#          User r1086364
#          ProxyJump r1086364@jump.extranet.kuleuven.be,r1086364@ssh.esat.kuleuven.be
#          IdentityFile ~/.ssh/id_ed25519
#
#   2. rclone remote "leroy" (SFTP, using the alias above):
#        rclone config create leroy sftp \
#          host leroy.esat.kuleuven.be \
#          user r1086364 \
#          key_file ~/.ssh/id_ed25519
#        (or point it at the "leroy" ssh alias if rclone supports it)
#
#   3. rclone remote "nas" (WebDAV pointing at your Nextcloud over Tailscale):
#        rclone config create nas webdav \
#          url https://<tailscale-nas-ip>/nextcloud/remote.php/dav/files/<user>/ \
#          vendor nextcloud \
#          user <nextcloud-user> \
#          pass <rclone-obscured-password>
#
#   4. Both this machine (Lex) and the NAS must be on the Tailscale network.
#
# Usage (from repo root on Lex):
#   bash scripts/pull_and_archive.sh
#   RCLONE_SRC=leroy:staged_data/archives_full500_v1 bash scripts/pull_and_archive.sh
#   RCLONE_DST=nas:Thesis/raw/staged_v2 bash scripts/pull_and_archive.sh
#
# Safe to run while collection is ongoing — rclone never deletes anything on leroy.
# Re-runs are resumable: --checksum means only new/changed archives are transferred.

set -euo pipefail

RCLONE_SRC="${RCLONE_SRC:-leroy:/volume1/scratch/r1086364/staged_data/archives_full500_v1}"
RCLONE_DST="${RCLONE_DST:-nas:Thesis/raw/staged}"
TRANSFERS="${TRANSFERS:-2}"   # concurrent rclone streams; 2 avoids saturating the uplink

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
die() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] $*" >&2; exit 1; }

command -v rclone >/dev/null || die "rclone not found. Install from https://rclone.org/install/"

log "====================================================="
log "pull_and_archive.sh"
log "  src : $RCLONE_SRC"
log "  dst : $RCLONE_DST"
log "  parallel transfers: $TRANSFERS"
log "====================================================="

# ── Count archives on leroy before transfer ───────────────────────────────────
log "Counting archives on leroy ..."
leroy_count=$(rclone ls "$RCLONE_SRC" 2>/dev/null | grep -c '\.tar\.zst$' || true)
leroy_bytes=$(rclone size "$RCLONE_SRC" --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('bytes',0))" 2>/dev/null || echo 0)
leroy_human=$(numfmt --to=iec-i --suffix=B "$leroy_bytes" 2>/dev/null || echo "${leroy_bytes} bytes")
log "leroy: $leroy_count archives  ($leroy_human)"

nas_count_before=$(rclone ls "$RCLONE_DST" 2>/dev/null | grep -c '\.tar\.zst$' || true)
log "NAS before: $nas_count_before archives"

# ── Copy (resumable, checksum-verified, read-only on source) ──────────────────
log "Starting rclone copy ..."
rclone copy \
    --checksum \
    --transfers "$TRANSFERS" \
    --progress \
    --stats-one-line \
    --immutable \
    "$RCLONE_SRC" "$RCLONE_DST"
log "rclone copy finished."

# ── Post-copy integrity check ─────────────────────────────────────────────────
log "Running rclone check (--one-way: every leroy archive must be on NAS) ..."
if rclone check \
    --checksum \
    --one-way \
    "$RCLONE_SRC" "$RCLONE_DST" 2>&1 | tee /tmp/rclone_check_output.txt; then
    log "rclone check: all archives verified on NAS."
else
    echo ""
    echo "!!! rclone check reported missing or mismatched archives:"
    cat /tmp/rclone_check_output.txt
    echo ""
    log "Re-run this script to retry failed transfers."
fi

# ── Summary ───────────────────────────────────────────────────────────────────
nas_count_after=$(rclone ls "$RCLONE_DST" 2>/dev/null | grep -c '\.tar\.zst$' || true)
newly_transferred=$(( nas_count_after - nas_count_before ))
nas_bytes=$(rclone size "$RCLONE_DST" --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('bytes',0))" 2>/dev/null || echo 0)
nas_human=$(numfmt --to=iec-i --suffix=B "$nas_bytes" 2>/dev/null || echo "${nas_bytes} bytes")

echo ""
log "====================================================="
log "Summary"
log "  Archives on leroy : $leroy_count"
log "  Archives on NAS   : $nas_count_after  ($nas_human)"
log "  Newly transferred : $newly_transferred"
log "====================================================="
