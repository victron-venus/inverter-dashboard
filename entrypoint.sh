#!/bin/bash
# Startup script — auto-update is disabled by default (set SELF_UPDATE_ENABLED=true to opt in)

cd /app

CONFIG_DIR="${INVERTER_DASHBOARD_CONFIG:-/app/config}"
export INVERTER_DASHBOARD_CONFIG="$CONFIG_DIR"

echo "=== Inverter Dashboard starting ==="
echo "Built-in version: $(cat VERSION 2>/dev/null || echo 'unknown')"
echo "Config dir (HA secrets + TLS): $CONFIG_DIR"

# Auto-update on startup (opt-in only)
if [[ "${SELF_UPDATE_ENABLED,,}" == "true" || "${SELF_UPDATE_ENABLED}" == "1" ]]; then
    if timeout 10 git fetch origin main 2>/dev/null; then
        echo "Fetched latest from GitHub"
        if git diff --quiet HEAD origin/main 2>/dev/null; then
            echo "Already up to date"
        else
            echo "Updating to latest version..."
            git reset --hard origin/main 2>/dev/null
            chmod +x /app/entrypoint.sh
            echo "Updated to: $(cat VERSION 2>/dev/null || git rev-parse --short HEAD)"
        fi
    else
        echo "GitHub unavailable, using built-in version"
    fi
else
    echo "Self-update disabled (set SELF_UPDATE_ENABLED=true to enable)"
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
    echo "NOTE: use 'wget --no-check-certificate https://127.0.0.1:$WEB_PORT/api/state' or 'curl -k https://127.0.0.1:$WEB_PORT/api/state' to test"
else
    echo "TLS: disabled (no cert+key in $CONFIG_DIR)"
fi

if [[ -z "$DASHBOARD_SECRET" ]]; then
    echo "WARNING: DASHBOARD_SECRET is not set — API endpoints are unprotected"
fi

echo "Starting server..."
exec python -m inverter_dashboard "${EXTRA[@]}" "$@"
