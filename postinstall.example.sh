#!/usr/bin/env bash
# Synology / host post-deploy helper.
# 1. Copy this file to postinstall.sh (postinstall.sh is gitignored).
# 2. Edit IMAGE, CONTAINER, HOST_CONFIG, MQTT_HOST, ports.
# 3. Place ha_secrets.py and optional dashboard.crt / dashboard.key under HOST_CONFIG.
# 4. chmod +x postinstall.sh && ./postinstall.sh

set -euo pipefail

IMAGE="${IMAGE:-alvit/inverter-dashboard:latest}"
CONTAINER="${CONTAINER:-inverter-dashboard}"
HOST_CONFIG="${HOST_CONFIG:-/volume1/docker/inverter-dashboard/config}"
MQTT_HOST="${MQTT_HOST:-192.168.160.150}"
PUBLISH_PORT="${PUBLISH_PORT:-8080}"

mkdir -p "$HOST_CONFIG"

echo ">>> Pull $IMAGE"
docker pull "$IMAGE"

echo ">>> Remove old container (forces fresh layer / latest)"
docker rm -f "$CONTAINER" 2>/dev/null || true

echo ">>> Run $CONTAINER"
docker run -d \
  --name "$CONTAINER" \
  --restart unless-stopped \
  -p "${PUBLISH_PORT}:8080" \
  -e MQTT_HOST="$MQTT_HOST" \
  -e MQTT_PORT="${MQTT_PORT:-1883}" \
  -e WEB_PORT=8080 \
  -e INVERTER_DASHBOARD_CONFIG=/app/config \
  -v "$HOST_CONFIG:/app/config:ro" \
  "$IMAGE"

echo ">>> Done. Open http(s)://<nas>:${PUBLISH_PORT} (HTTPS if dashboard.crt+key in config)."
