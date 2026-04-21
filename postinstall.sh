#!/usr/bin/env bash
# Synology NAS: sync secrets/certs into the mounted config dir and recreate the dashboard container.
# Paths match portainer-stack.yml / docker-compose.yml.
#
# Usage (on NAS, from repo clone or copy this script):
#   chmod +x postinstall.sh
#   ./postinstall.sh
#
# Optional env:
#   SOURCE_CONFIG  — directory containing ha_secrets.py (default: ./config next to this script)
#   STACK_FILE     — compose file (default: ./portainer-stack.yml next to this script)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOST_CONFIG="${HOST_CONFIG:-/volume1/docker/inverter-dashboard/config}"
SOURCE_CONFIG="${SOURCE_CONFIG:-$SCRIPT_DIR/config}"
STACK_FILE="${STACK_FILE:-$SCRIPT_DIR/portainer-stack.yml}"
IMAGE="${IMAGE:-alvit/inverter-dashboard:latest}"
MQTT_HOST="${MQTT_HOST:-192.168.160.150}"

mkdir -p "$HOST_CONFIG"

if [[ -f "$SOURCE_CONFIG/ha_secrets.py" ]]; then
  install -m 600 "$SOURCE_CONFIG/ha_secrets.py" "$HOST_CONFIG/ha_secrets.py"
  echo ">>> Installed ha_secrets.py -> $HOST_CONFIG/"
else
  echo "WARNING: $SOURCE_CONFIG/ha_secrets.py not found — create it (copy from ha_secrets.example.py)." >&2
fi

for f in dashboard.crt dashboard.key; do
  if [[ -f "$SOURCE_CONFIG/$f" ]]; then
    install -m 644 "$SOURCE_CONFIG/$f" "$HOST_CONFIG/$f"
    echo ">>> Installed $f -> $HOST_CONFIG/"
  fi
done

echo ">>> docker pull $IMAGE"
docker pull "$IMAGE"

if [[ -f "$STACK_FILE" ]]; then
  echo ">>> Recreate stack service (compose v2)"
  docker compose -f "$STACK_FILE" pull inverter-dashboard
  docker compose -f "$STACK_FILE" up -d --force-recreate inverter-dashboard
else
  echo "WARNING: $STACK_FILE not found — falling back to plain docker run (no Watchtower stack)." >&2
  CONTAINER="${CONTAINER:-inverter-dashboard}"
  docker rm -f "$CONTAINER" 2>/dev/null || true
  docker run -d \
    --name "$CONTAINER" \
    --restart unless-stopped \
    -p "${PUBLISH_PORT:-8080}:8080" \
    -e MQTT_HOST="$MQTT_HOST" \
    -e MQTT_PORT="${MQTT_PORT:-1883}" \
    -e WEB_PORT=8080 \
    -e INVERTER_DASHBOARD_CONFIG=/app/config \
    -v "$HOST_CONFIG:/app/config:ro" \
    "$IMAGE"
fi

echo ">>> Done. Config: $HOST_CONFIG — HTTPS if dashboard.crt + dashboard.key present."
