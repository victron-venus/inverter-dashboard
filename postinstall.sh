#!/usr/bin/env bash
# Run from your Mac / PC: copy config + stack to Synology over SSH, then recreate the container there.
#
# Prerequisites: SSH key login to Synology (DSM user with docker rights), Docker/Compose on NAS.
#
#   export SYNOLOGY_SSH='admin@192.168.x.x'   # or Host from ~/.ssh/config
#   ./postinstall.sh
#
# Optional env:
#   SOURCE_CONFIG   — local dir with ha_secrets.py & optional certs (default: ./config next to this script)
#   REMOTE_BASE     — on NAS (default: /volume1/docker/inverter-dashboard)
#   STACK_FILE      — local compose file to upload (default: ./portainer-stack.yml)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ -z "${SYNOLOGY_SSH:-}" ]]; then
  echo "Set SYNOLOGY_SSH to your NAS, e.g. export SYNOLOGY_SSH='admin@192.168.1.20'" >&2
  exit 1
fi

SOURCE_CONFIG="${SOURCE_CONFIG:-$SCRIPT_DIR/config}"
REMOTE_BASE="${REMOTE_BASE:-/volume1/docker/inverter-dashboard}"
REMOTE_CONFIG="${REMOTE_BASE}/config"
STACK_FILE="${STACK_FILE:-$SCRIPT_DIR/portainer-stack.yml}"
IMAGE="${IMAGE:-alvit/inverter-dashboard:latest}"

echo ">>> NAS: $SYNOLOGY_SSH"
echo ">>> Remote paths: $REMOTE_CONFIG, stack $REMOTE_BASE/portainer-stack.yml"

ssh "$SYNOLOGY_SSH" "mkdir -p \"$REMOTE_CONFIG\" \"$REMOTE_BASE\""

install_one() {
  local src="$1" name="$2"
  if [[ ! -f "$src" ]]; then
    echo "SKIP (missing): $src" >&2
    return 0
  fi
  scp -p "$src" "${SYNOLOGY_SSH}:${REMOTE_CONFIG}/${name}"
  echo ">>> Copied -> ${REMOTE_CONFIG}/${name}"
}

# Prefer config/ha_secrets.py; else repo root ha_secrets.py
if [[ -f "$SOURCE_CONFIG/ha_secrets.py" ]]; then
  install_one "$SOURCE_CONFIG/ha_secrets.py" "ha_secrets.py"
elif [[ -f "$SCRIPT_DIR/ha_secrets.py" ]]; then
  install_one "$SCRIPT_DIR/ha_secrets.py" "ha_secrets.py"
else
  echo "WARNING: no ha_secrets.py in $SOURCE_CONFIG or $SCRIPT_DIR — add before deploy." >&2
fi

for f in dashboard.crt dashboard.key; do
  if [[ -f "$SOURCE_CONFIG/$f" ]]; then
    install_one "$SOURCE_CONFIG/$f" "$f"
  elif [[ -f "$SCRIPT_DIR/.certs/$f" ]]; then
    install_one "$SCRIPT_DIR/.certs/$f" "$f"
  fi
done

if [[ ! -f "$STACK_FILE" ]]; then
  echo "ERROR: $STACK_FILE not found — cannot upload stack / run compose." >&2
  exit 1
fi
scp -p "$STACK_FILE" "${SYNOLOGY_SSH}:${REMOTE_BASE}/portainer-stack.yml"
echo ">>> Uploaded portainer-stack.yml"

# Tight permissions for secrets on NAS
ssh "$SYNOLOGY_SSH" "chmod 600 \"${REMOTE_CONFIG}/ha_secrets.py\" 2>/dev/null || true; chmod 644 \"${REMOTE_CONFIG}/dashboard.crt\" \"${REMOTE_CONFIG}/dashboard.key\" 2>/dev/null || true"

echo ">>> Remote: docker pull + recreate inverter-dashboard"
ssh "$SYNOLOGY_SSH" "docker pull \"$IMAGE\" && docker compose -f \"${REMOTE_BASE}/portainer-stack.yml\" pull inverter-dashboard && docker compose -f \"${REMOTE_BASE}/portainer-stack.yml\" up -d --force-recreate inverter-dashboard"

echo ">>> Done. Open NAS:8080 (or your published port). HTTPS if dashboard.crt+key were copied."
