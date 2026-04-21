#!/bin/bash
# Auto-update on startup: fetch latest code from GitHub, fallback to built-in

cd /app

CONFIG_DIR="${INVERTER_DASHBOARD_CONFIG:-/app/config}"
export INVERTER_DASHBOARD_CONFIG="$CONFIG_DIR"

echo "=== Inverter Dashboard starting ==="
echo "Built-in version: $(cat VERSION 2>/dev/null || echo 'unknown')"
echo "Config dir (HA secrets + TLS): $CONFIG_DIR"

# Try to update from GitHub (timeout 10s)
if timeout 10 git fetch origin main 2>/dev/null; then
    echo "Fetched latest from GitHub"
    if git diff --quiet HEAD origin/main 2>/dev/null; then
        echo "Already up to date"
    else
        echo "Updating to latest version..."
        git reset --hard origin/main 2>/dev/null
        echo "Updated to: $(cat VERSION 2>/dev/null || git rev-parse --short HEAD)"
    fi
else
    echo "GitHub unavailable, using built-in version"
fi

SSL_CERT="${CONFIG_DIR}/dashboard.crt"
SSL_KEY="${CONFIG_DIR}/dashboard.key"
EXTRA=()
has_manual_ssl=false
for a in "$@"; do
    if [[ "$a" == --ssl-cert ]]; then
        has_manual_ssl=true
        break
    fi
done
if ! $has_manual_ssl && [[ -f "$SSL_CERT" && -f "$SSL_KEY" ]]; then
    EXTRA+=(--ssl-cert "$SSL_CERT" --ssl-key "$SSL_KEY")
    echo "TLS: enabled using $SSL_CERT (same port as --port / WEB_PORT)"
fi

echo "Starting server..."
exec python server.py "${EXTRA[@]}" "$@"
