#!/usr/bin/env bash
# Run from your Mac / PC: copy config + stack to Synology over SSH, then recreate the container there.
#
# Assumes:
#   - **~/.ssh/config** defines **`Host synology`** (user, hostname, IdentityFile — nothing hardcoded here).
#   - SSH uses that host alias: **`ssh synology`** (override with **SYNOLOGY_SSH** if needed).
#   - **sudo** works without password for mkdir/cp/chmod and **docker** / **docker compose** on the NAS.
#   - Files are sent with **ssh + stdin** (not scp): Synology often returns
#     "subsystem request failed on channel 0" for scp/sftp when the SFTP subsystem is off or misconfigured.
#
#   ./postinstall.sh
#
# Optional env:
#   SYNOLOGY_SSH   — SSH destination (default: synology)
#   SOURCE_CONFIG  — local dir with ha_secrets.py & optional certs (default: ./config)
#   REMOTE_BASE    — on NAS (default: /volume1/docker/inverter-dashboard)
#   STACK_FILE     — local compose file to upload (default: ./portainer-stack.yml)
#   DOCKER         — prefix for docker CLI (default: sudo docker)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

SYNOLOGY_SSH="${SYNOLOGY_SSH:-synology}"

SOURCE_CONFIG="${SOURCE_CONFIG:-$SCRIPT_DIR/config}"
REMOTE_BASE="${REMOTE_BASE:-/volume1/docker/inverter-dashboard}"
REMOTE_CONFIG="${REMOTE_BASE}/config"
STACK_FILE="${STACK_FILE:-$SCRIPT_DIR/portainer-stack.yml}"
IMAGE="${IMAGE:-alvit/inverter-dashboard:latest}"
DOCKER="${DOCKER:-sudo docker}"

echo ">>> NAS: $SYNOLOGY_SSH"
echo ">>> Remote: $REMOTE_CONFIG , $REMOTE_BASE/portainer-stack.yml"
echo ">>> Docker: $DOCKER ..."

STAGING=$(ssh "$SYNOLOGY_SSH" 'mktemp -d /tmp/inverter-dash.XXXXXX')
cleanup() { ssh "$SYNOLOGY_SSH" "rm -rf \"$STAGING\"" 2>/dev/null || true; }
trap cleanup EXIT

ssh "$SYNOLOGY_SSH" "sudo mkdir -p \"$REMOTE_CONFIG\" \"$REMOTE_BASE\""

install_file() {
  local src="$1" name="$2" mode="$3"
  if [[ ! -f "$src" ]]; then
    echo "SKIP (missing): $src" >&2
    return 0
  fi
  ssh "$SYNOLOGY_SSH" "cat > \"${STAGING}/${name}\"" < "$src"
  ssh "$SYNOLOGY_SSH" "sudo install -m \"${mode}\" \"${STAGING}/${name}\" \"${REMOTE_CONFIG}/${name}\""
  echo ">>> Installed -> ${REMOTE_CONFIG}/${name} (${mode})"
}

if [[ -f "$SOURCE_CONFIG/ha_secrets.py" ]]; then
  install_file "$SOURCE_CONFIG/ha_secrets.py" "ha_secrets.py" "600"
elif [[ -f "$SCRIPT_DIR/ha_secrets.py" ]]; then
  install_file "$SCRIPT_DIR/ha_secrets.py" "ha_secrets.py" "600"
else
  echo "WARNING: no ha_secrets.py — add under $SOURCE_CONFIG or repo root." >&2
fi

for f in dashboard.crt dashboard.key; do
  if [[ -f "$SOURCE_CONFIG/$f" ]]; then
    install_file "$SOURCE_CONFIG/$f" "$f" "644"
  elif [[ -f "$SCRIPT_DIR/.certs/$f" ]]; then
    install_file "$SCRIPT_DIR/.certs/$f" "$f" "644"
  fi
done

if [[ ! -f "$STACK_FILE" ]]; then
  echo "ERROR: $STACK_FILE not found." >&2
  exit 1
fi
ssh "$SYNOLOGY_SSH" "cat > \"${STAGING}/portainer-stack.yml\"" < "$STACK_FILE"
ssh "$SYNOLOGY_SSH" "sudo install -m 644 \"${STAGING}/portainer-stack.yml\" \"${REMOTE_BASE}/portainer-stack.yml\""
echo ">>> Uploaded portainer-stack.yml"

echo ">>> Remote: sudo docker pull + compose recreate"
# shellcheck disable=SC2086
ssh "$SYNOLOGY_SSH" "${DOCKER} pull \"${IMAGE}\" && ${DOCKER} compose -f \"${REMOTE_BASE}/portainer-stack.yml\" pull inverter-dashboard && ${DOCKER} compose -f \"${REMOTE_BASE}/portainer-stack.yml\" up -d --force-recreate inverter-dashboard"

echo ">>> Done. HTTPS if dashboard.crt + dashboard.key were deployed."
