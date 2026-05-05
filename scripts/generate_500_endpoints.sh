#!/usr/bin/env bash
# scripts/generate_500_endpoints.sh
#
# Generates 375 new web-server endpoints to reach 500 URLs total.
#
# What it does:
#   1. Run gen_500_content.py  → produces HTML + JSON in webserver/pages/
#   2. rsync HTML + JSON       → web server /var/www/html/
#   3. SSH: create binary stubs (pdf/mp3/mp4/zip) with dd / python3
#   4. SSH: extend nginx no-cache conf.d snippet
#   5. Update config/urls.txt
#   6. Update config/urls_nym5_extended.txt
#   7. Smoke-test all 375 new endpoints (HTTP 200 + size > 0)
#
# Binary size schedules:
#   doc_pdf  11-50 : 500KB 1MB 2MB 5MB  (×10 each)
#   audio_mp3 11-50: 1MB 3MB 5MB 8MB 10MB (×8 each)
#   video_mp4 11-50: 5MB 10MB 20MB 42MB  (×10 each)
#   archive_zip 11-85: 1MB 5MB 10MB 20MB (cycling)
#
# New URL counts:
#   40 page_html_heavy + 60 news/shop/social/forum + 40 data_json
#   + 40 crypto/api JSON + 40 pdf + 40 mp3 + 40 mp4 + 75 zip = 375

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PAGES_DIR="$REPO_ROOT/webserver/pages"
URLS_FILE="$REPO_ROOT/config/urls.txt"
NYM5_FILE="$REPO_ROOT/config/urls_nym5_extended.txt"

WEB_SERVER="root@204.168.163.45"
WEB_ROOT="/var/www/html"
SSH_KEY="$HOME/.ssh/nico-thesis"
SSH_OPTS="-i $SSH_KEY -o StrictHostKeyChecking=no -o ConnectTimeout=20"

NGINX_CONF_PATH="/etc/nginx/conf.d/500-endpoints-no-cache.conf"

sep() { echo; echo "══════════════════════════════════════════════════════════════"; }

sep
echo "  generate_500_endpoints.sh — building 375 new endpoints"
sep

# ── Step 1: generate HTML + JSON locally ──────────────────────────────────
echo
echo "[1/7] Generating HTML and JSON files via gen_500_content.py …"
python3 "$REPO_ROOT/scripts/gen_500_content.py"
echo "  Done."

# ── Step 2: rsync HTML + JSON to web server ───────────────────────────────
echo
echo "[2/7] rsyncing webserver/pages/ to ${WEB_SERVER}:${WEB_ROOT}/ …"
rsync -avz --progress \
    -e "ssh $SSH_OPTS" \
    --include="*.html" \
    --include="*.json" \
    --exclude="*" \
    "$PAGES_DIR/" \
    "${WEB_SERVER}:${WEB_ROOT}/"
echo "  rsync done."

# ── Step 3: create binary stubs on the web server ─────────────────────────
echo
echo "[3/7] Creating binary stubs on web server (pdf / mp3 / mp4 / zip) …"

# Size lookup helpers — returns bytes
KB=$((1024))
MB=$((1024*1024))

ssh $SSH_OPTS "$WEB_SERVER" bash <<'REMOTE'
set -euo pipefail
cd /var/www/html

echo "  → doc_pdf_11..50"
PDF_SIZES=(
  500 1024 2048 5120          # pattern: 500KB 1MB 2MB 5MB (×10 each)
  500 1024 2048 5120
  500 1024 2048 5120
  500 1024 2048 5120
  500 1024 2048 5120
  500 1024 2048 5120
  500 1024 2048 5120
  500 1024 2048 5120
  500 1024 2048 5120
  500 1024 2048 5120
)
for i in $(seq 11 50); do
  idx=$((i - 11))
  size_kb=${PDF_SIZES[$idx]}
  fname="doc_pdf_${i}.pdf"
  [[ -f "$fname" ]] && echo "    skip $fname" && continue
  # Prepend valid PDF header so nginx serves correct MIME
  python3 -c "
import os, sys
size = $size_kb * 1024
header = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n'
body   = os.urandom(size - len(header))
sys.stdout.buffer.write(header + body)
" > "$fname"
  echo "    created $fname (${size_kb} KB)"
done

echo "  → audio_mp3_11..50"
MP3_SIZES=(1024 3072 5120 8192 10240  1024 3072 5120 8192 10240
           1024 3072 5120 8192 10240  1024 3072 5120 8192 10240
           1024 3072 5120 8192 10240  1024 3072 5120 8192 10240
           1024 3072 5120 8192 10240  1024 3072 5120 8192 10240)
for i in $(seq 11 50); do
  idx=$((i - 11))
  size_kb=${MP3_SIZES[$idx]}
  fname="audio_mp3_${i}.mp3"
  [[ -f "$fname" ]] && echo "    skip $fname" && continue
  python3 -c "
import os, sys
size = $size_kb * 1024
# ID3v2 header stub so browsers recognise the MIME
header = b'ID3\x04\x00\x00\x00\x00\x00\x00'
body   = os.urandom(size - len(header))
sys.stdout.buffer.write(header + body)
" > "$fname"
  echo "    created $fname (${size_kb} KB)"
done

echo "  → video_mp4_11..50"
MP4_SIZES=(5120 10240 20480 43008  5120 10240 20480 43008
           5120 10240 20480 43008  5120 10240 20480 43008
           5120 10240 20480 43008  5120 10240 20480 43008
           5120 10240 20480 43008  5120 10240 20480 43008
           5120 10240 20480 43008  5120 10240 20480 43008)
for i in $(seq 11 50); do
  idx=$((i - 11))
  size_kb=${MP4_SIZES[$idx]}
  fname="video_mp4_${i}.mp4"
  [[ -f "$fname" ]] && echo "    skip $fname" && continue
  python3 -c "
import os, sys
size = $size_kb * 1024
# ftyp box: isom brand — minimal valid MP4 header
ftyp = b'\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isommp42'
body = os.urandom(size - len(ftyp))
sys.stdout.buffer.write(ftyp + body)
" > "$fname"
  echo "    created $fname (${size_kb} KB)"
done

echo "  → archive_zip_11..85"
ZIP_SIZES=(1024 5120 10240 20480  1024 5120 10240 20480
           1024 5120 10240 20480  1024 5120 10240 20480
           1024 5120 10240 20480  1024 5120 10240 20480
           1024 5120 10240 20480  1024 5120 10240 20480
           1024 5120 10240 20480  1024 5120 10240 20480
           1024 5120 10240 20480  1024 5120 10240 20480
           1024 5120 10240 20480  1024 5120 10240 20480
           1024 5120 10240 20480  1024 5120 10240 20480
           1024 5120 10240 20480  1024 5120)
for i in $(seq 11 85); do
  idx=$((i - 11))
  size_kb=${ZIP_SIZES[$idx]}
  fname="archive_zip_${i}.zip"
  [[ -f "$fname" ]] && echo "    skip $fname" && continue
  python3 -c "
import io, os, zipfile
size_target = $size_kb * 1024
buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w', zipfile.ZIP_STORED) as zf:
    chunk = 65536
    written = 0
    part = 0
    while written < size_target - 512:
        to_write = min(chunk, size_target - 512 - written)
        zf.writestr(f'data_{part:04d}.bin', os.urandom(to_write))
        written += to_write
        part += 1
import sys
sys.stdout.buffer.write(buf.getvalue())
" > "$fname"
  echo "    created $fname (${size_kb} KB)"
done

echo "  Binary stubs done."
REMOTE

echo "  Remote binary generation complete."

# ── Step 4: update nginx no-cache config ──────────────────────────────────
echo
echo "[4/7] Writing nginx no-cache conf.d snippet …"

ssh $SSH_OPTS "$WEB_SERVER" "cat > ${NGINX_CONF_PATH}" <<'NGINX_EOF'
# Auto-generated by generate_500_endpoints.sh — do not edit by hand.
# Sets Cache-Control: no-cache for all research HTML and JSON pages.
server {
    listen 80 default_server;
    server_name _;
    root /var/www/html;

    # Heavy HTML pages (all variants)
    location ~* ^/page_(html_heavy|news|shop|social|forum|html|heavy_gallery|heavy_dashboard|heavy_docs)_[0-9]+\.html$ {
        add_header Cache-Control "no-cache, no-store, must-revalidate" always;
        add_header Pragma        "no-cache" always;
        add_header Expires       "0" always;
    }

    # JSON data endpoints
    location ~* ^/(data_json|crypto_).*\.json$ {
        add_header Cache-Control "no-cache, no-store, must-revalidate" always;
        add_header Pragma        "no-cache" always;
        add_header Expires       "0" always;
        add_header Content-Type  "application/json" always;
    }

    # Binary files — allow caching (sizes are fixed)
    location ~* \.(pdf|mp3|mp4|zip)$ {
        add_header Cache-Control "public, max-age=3600" always;
    }

    # Assets
    location /assets/ {
        expires 1h;
        add_header Cache-Control "public" always;
    }
}
NGINX_EOF

ssh $SSH_OPTS "$WEB_SERVER" "nginx -t && systemctl reload nginx"
echo "  nginx reloaded."

# ── Step 5: update config/urls.txt ────────────────────────────────────────
echo
echo "[5/7] Updating $URLS_FILE …"

add_url() {
    local entry="$1"
    if grep -qxF "$entry" "$URLS_FILE" 2>/dev/null; then
        return 0
    fi
    echo "$entry" >> "$URLS_FILE"
}

# html_heavy 11-50
for i in $(seq 11 50);  do add_url "page_html_heavy_${i}.html"; done
# news/shop/social/forum 6-20
for i in $(seq 6 20);   do add_url "page_news_${i}.html";   done
for i in $(seq 6 20);   do add_url "page_shop_${i}.html";   done
for i in $(seq 6 20);   do add_url "page_social_${i}.html"; done
for i in $(seq 6 20);   do add_url "page_forum_${i}.html";  done
# data_json 11-50
for i in $(seq 11 50);  do add_url "data_json_${i}.json";  done
# crypto/api json
for i in $(seq 1 8); do
    add_url "crypto_market_data_${i}.json"
    add_url "crypto_portfolio_${i}.json"
    add_url "crypto_orderbook_${i}.json"
    add_url "crypto_analytics_${i}.json"
    add_url "crypto_metrics_${i}.json"
done
# binary stubs
for i in $(seq 11 50); do add_url "doc_pdf_${i}.pdf";    done
for i in $(seq 11 50); do add_url "audio_mp3_${i}.mp3";  done
for i in $(seq 11 50); do add_url "video_mp4_${i}.mp4";  done
for i in $(seq 11 85); do add_url "archive_zip_${i}.zip"; done

TOTAL=$(grep -c . "$URLS_FILE" || true)
echo "  urls.txt now has $TOTAL entries."

# ── Step 6: update urls_nym5_extended.txt (HTML + JSON only) ───────────────
echo
echo "[6/7] Updating $NYM5_FILE (HTML + JSON only) …"

add_nym5() {
    local entry="$1"
    if grep -qxF "$entry" "$NYM5_FILE" 2>/dev/null; then
        return 0
    fi
    echo "$entry" >> "$NYM5_FILE"
}

# html_heavy 11-50
for i in $(seq 11 50); do add_nym5 "page_html_heavy_${i}.html"; done
# news/shop/social/forum 6-20
for i in $(seq 6 20);  do add_nym5 "page_news_${i}.html";   done
for i in $(seq 6 20);  do add_nym5 "page_shop_${i}.html";   done
for i in $(seq 6 20);  do add_nym5 "page_social_${i}.html"; done
for i in $(seq 6 20);  do add_nym5 "page_forum_${i}.html";  done
# data_json 11-50
for i in $(seq 11 50); do add_nym5 "data_json_${i}.json";   done
# crypto/api
for i in $(seq 1 8); do
    add_nym5 "crypto_market_data_${i}.json"
    add_nym5 "crypto_portfolio_${i}.json"
    add_nym5 "crypto_orderbook_${i}.json"
    add_nym5 "crypto_analytics_${i}.json"
    add_nym5 "crypto_metrics_${i}.json"
done

NYM5_TOTAL=$(grep -c "^[^#]" "$NYM5_FILE" || true)
echo "  urls_nym5_extended.txt now has $NYM5_TOTAL non-comment entries."

# ── Step 7: smoke-test all 375 new endpoints ───────────────────────────────
echo
echo "[7/7] Smoke-testing all 375 new endpoints (HTTP 200, size > 0) …"
echo

PASS=0
FAIL=0
FAIL_LIST=""

check_url() {
    local fname="$1"
    local url="http://localhost/${fname}"
    local result
    result=$(ssh $SSH_OPTS "$WEB_SERVER" \
        "curl -s -o /dev/null -w '%{http_code} %{size_download}' '${url}'" 2>/dev/null || echo "000 0")
    local code size
    code=$(echo "$result" | awk '{print $1}')
    size=$(echo "$result" | awk '{print $2}')
    if [[ "$code" == "200" && "$size" -gt 0 ]]; then
        PASS=$((PASS+1))
    else
        FAIL=$((FAIL+1))
        FAIL_LIST="$FAIL_LIST\n  ✗ $fname  HTTP $code  ${size} bytes"
        echo "  ✗ $fname  HTTP $code  ${size} bytes"
    fi
}

# Batch check — run 10 at a time via a single SSH call for speed
batch_check() {
    local files=("$@")
    # Build a one-liner that checks all files and returns "fname:code:size" per line
    local cmds=""
    for f in "${files[@]}"; do
        cmds+="echo \"${f}:\$(curl -s -o /dev/null -w '%{http_code}:%{size_download}' 'http://localhost/${f}')\"; "
    done
    ssh $SSH_OPTS "$WEB_SERVER" "bash -c '$cmds'" 2>/dev/null
}

run_batch() {
    local label="$1"; shift
    local files=("$@")
    echo "  Checking $label (${#files[@]} URLs) …"
    local batch_size=20
    local i=0
    while [[ $i -lt ${#files[@]} ]]; do
        local chunk=("${files[@]:$i:$batch_size}")
        while IFS=: read -r fname code size; do
            [[ -z "$fname" ]] && continue
            if [[ "$code" == "200" && "$size" -gt 0 ]]; then
                PASS=$((PASS+1))
            else
                FAIL=$((FAIL+1))
                FAIL_LIST="${FAIL_LIST}"$'\n'"  ✗ ${fname}  HTTP ${code}  ${size} bytes"
                echo "    ✗ ${fname}  HTTP ${code}  ${size} bytes"
            fi
        done < <(batch_check "${chunk[@]}")
        i=$((i + batch_size))
    done
}

# Build arrays
HEAVY_FILES=(); for i in $(seq 11 50);  do HEAVY_FILES+=("page_html_heavy_${i}.html"); done
NEWS_FILES=();  for i in $(seq 6 20);   do NEWS_FILES+=("page_news_${i}.html");        done
SHOP_FILES=();  for i in $(seq 6 20);   do SHOP_FILES+=("page_shop_${i}.html");        done
SOCIAL_FILES=();for i in $(seq 6 20);   do SOCIAL_FILES+=("page_social_${i}.html");    done
FORUM_FILES=(); for i in $(seq 6 20);   do FORUM_FILES+=("page_forum_${i}.html");      done
JSON_FILES=();  for i in $(seq 11 50);  do JSON_FILES+=("data_json_${i}.json");        done
for i in $(seq 1 8); do
    JSON_FILES+=("crypto_market_data_${i}.json" "crypto_portfolio_${i}.json"
                 "crypto_orderbook_${i}.json"   "crypto_analytics_${i}.json"
                 "crypto_metrics_${i}.json")
done
PDF_FILES=();  for i in $(seq 11 50); do PDF_FILES+=("doc_pdf_${i}.pdf");        done
MP3_FILES=();  for i in $(seq 11 50); do MP3_FILES+=("audio_mp3_${i}.mp3");      done
MP4_FILES=();  for i in $(seq 11 50); do MP4_FILES+=("video_mp4_${i}.mp4");      done
ZIP_FILES=();  for i in $(seq 11 85); do ZIP_FILES+=("archive_zip_${i}.zip");    done

run_batch "page_html_heavy (40)"  "${HEAVY_FILES[@]}"
run_batch "page_news (15)"        "${NEWS_FILES[@]}"
run_batch "page_shop (15)"        "${SHOP_FILES[@]}"
run_batch "page_social (15)"      "${SOCIAL_FILES[@]}"
run_batch "page_forum (15)"       "${FORUM_FILES[@]}"
run_batch "data_json (40)"        "${JSON_FILES[@]}"
run_batch "doc_pdf (40)"          "${PDF_FILES[@]}"
run_batch "audio_mp3 (40)"        "${MP3_FILES[@]}"
run_batch "video_mp4 (40)"        "${MP4_FILES[@]}"
run_batch "archive_zip (75)"      "${ZIP_FILES[@]}"

sep
echo "  Smoke-test results: $PASS passed, $FAIL failed"
if [[ $FAIL -gt 0 ]]; then
    echo -e "  Failed endpoints:$FAIL_LIST"
    sep
    echo "  WARNING: $FAIL endpoint(s) did not return HTTP 200."
    echo "  Check nginx on $WEB_SERVER: journalctl -u nginx -n 50"
    sep
    exit 1
fi

TOTAL_URLS=$(grep -c . "$URLS_FILE" || true)
NYM5_URLS=$(grep -c "^[^#]" "$NYM5_FILE" || true)
sep
echo "  All $PASS new endpoints verified successfully."
echo "  Total URLs in urls.txt       : $TOTAL_URLS"
echo "  Total URLs in nym5_extended  : $NYM5_URLS"
sep
