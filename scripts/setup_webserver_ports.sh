#!/usr/bin/env bash
# scripts/setup_webserver_ports.sh
# ==================================
# Adds listen directives for ports 8080-8083 to the nginx server block on
# the web server VM, so each collection mode gets an isolated egress BPF port.
#
# Port assignment:
#   80   — baseline (default, already live)
#   8080 — vpn
#   8081 — tor
#   8082 — nym5
#   8083 — nym2
#
# The script is idempotent: re-running it is safe.
#
# Usage (from repo root):
#   bash scripts/setup_webserver_ports.sh

set -euo pipefail

SSH_KEY="$HOME/.ssh/nico-thesis"
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=15 -i $SSH_KEY"
TARGET="root@204.168.163.45"
PRIVATE_IP="10.1.0.3"

echo "========================================"
echo " setup_webserver_ports.sh"
echo " Target: $TARGET ($PRIVATE_IP)"
echo "========================================"

# ── 1. Find the nginx server block config file ────────────────────────────────
echo ""
echo "[1/4] Locating nginx server block..."
NGINX_CONF=$(ssh $SSH_OPTS "$TARGET" "
    # Try the most common locations in order
    for f in \
        /etc/nginx/sites-enabled/default \
        /etc/nginx/conf.d/default.conf \
        /etc/nginx/nginx.conf; do
        if [[ -f \"\$f\" ]] && grep -q 'listen' \"\$f\" 2>/dev/null; then
            echo \"\$f\"
            break
        fi
    done
")

if [[ -z "$NGINX_CONF" ]]; then
    echo "[error] Could not find nginx config with a listen directive."
    exit 1
fi
echo "  Found: $NGINX_CONF"

# ── 2. Inject additional listen directives if not already present ─────────────
echo ""
echo "[2/4] Adding listen 8080-8083 to server block..."
ssh $SSH_OPTS "$TARGET" "
    set -euo pipefail
    CONF='$NGINX_CONF'

    # Back up once (keep the first backup, don't overwrite on re-runs)
    [[ -f \"\${CONF}.bak\" ]] || cp \"\$CONF\" \"\${CONF}.bak\"

    # Insert the four extra listen lines after the first 'listen 80' line,
    # but only if they are not already present.
    if grep -q 'listen 8080' \"\$CONF\"; then
        echo '  Already present — skipping injection.'
    else
        # Use sed to insert after the line containing 'listen 80;'
        # Works for both 'listen 80;' and 'listen 80 default_server;'
        sed -i '/listen 80/a\\    listen 8080;\\n    listen 8081;\\n    listen 8082;\\n    listen 8083;' \"\$CONF\"
        echo '  Injected listen 8080-8083.'
    fi
"

# ── 3. Test config and reload nginx ──────────────────────────────────────────
echo ""
echo "[3/4] Testing nginx config and reloading..."
ssh $SSH_OPTS "$TARGET" "nginx -t && systemctl reload nginx"
echo "  nginx reloaded OK."

# ── 4. Verify all 5 ports respond with 200 ───────────────────────────────────
echo ""
echo "[4/4] Verifying port responses from web server (loopback curl)..."
FAILED=0
for PORT in 80 8080 8081 8082 8083; do
    CODE=$(ssh $SSH_OPTS "$TARGET" \
        "curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
         http://${PRIVATE_IP}:${PORT}/page_html_1.html || echo 000")
    if [[ "$CODE" == "200" ]]; then
        echo "  port $PORT — $CODE OK"
    else
        echo "  port $PORT — $CODE FAIL"
        FAILED=1
    fi
done

echo ""
if [[ "$FAILED" -eq 0 ]]; then
    echo "========================================"
    echo " All 5 ports live. Web server ready."
    echo "========================================"
else
    echo "[error] One or more ports failed. Check nginx config on $TARGET."
    exit 1
fi
