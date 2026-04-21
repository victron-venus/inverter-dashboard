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
#   DOCKER         — prefix for docker CLI (default: sudo /usr/local/bin/docker — Synology PATH under sudo often lacks docker)
#   SKIP_MAC_TRUST — set to 1 to skip importing dashboard.crt into macOS Keychain
#   AUTO_GENERATE_DASHBOARD_TLS — if 1 (default), run scripts/ssl-local-deploy.sh when no full
#       dashboard.crt + dashboard.key pair exists under SOURCE_CONFIG or .certs/ (skipped if either folder already has both files).
#   TLS_CN — CN/SAN hostname for generated cert (default inverter-dashboard.local).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

SYNOLOGY_SSH="${SYNOLOGY_SSH:-synology}"

SOURCE_CONFIG="${SOURCE_CONFIG:-$SCRIPT_DIR/config}"
REMOTE_BASE="${REMOTE_BASE:-/volume1/docker/inverter-dashboard}"
REMOTE_CONFIG="${REMOTE_BASE}/config"
STACK_FILE="${STACK_FILE:-$SCRIPT_DIR/portainer-stack.yml}"
IMAGE="${IMAGE:-alvit/inverter-dashboard:latest}"
DOCKER="${DOCKER:-sudo /usr/local/bin/docker}"
AUTO_GENERATE_DASHBOARD_TLS="${AUTO_GENERATE_DASHBOARD_TLS:-1}"

dashboard_tls_pair_present() {
  [[ -f "$SOURCE_CONFIG/dashboard.crt" && -f "$SOURCE_CONFIG/dashboard.key" ]] && return 0
  [[ -f "$SCRIPT_DIR/.certs/dashboard.crt" && -f "$SCRIPT_DIR/.certs/dashboard.key" ]] && return 0
  return 1
}

maybe_generate_dashboard_tls() {
  [[ "${AUTO_GENERATE_DASHBOARD_TLS}" == "1" ]] || return 0
  if dashboard_tls_pair_present; then
    echo ">>> TLS: dashboard.crt + dashboard.key already present — skip generation."
    return 0
  fi
  echo ">>> TLS: generating dashboard.crt + dashboard.key into .certs/ ..."
  CERT_DIR="$SCRIPT_DIR/.certs" TLS_CN="${TLS_CN:-inverter-dashboard.local}" \
    "$SCRIPT_DIR/scripts/ssl-local-deploy.sh"
}

# On macOS: trust local dashboard.crt if not already present (HTTPS in Safari/Chrome).
# Some macOS builds lack /Library/Keychains/System.keychain-db — try System.keychain, then login.keychain-db (no sudo).

# Before importing: drop prior copies (same fingerprint or same CN) to avoid duplicates / stale certs after regeneration.
remove_dashboard_cert_from_mac_keychains() {
  local cert="$1" hash="$2" cn="$3"
  local k
  for k in \
      /Library/Keychains/System.keychain-db \
      /Library/Keychains/System.keychain \
      "${HOME}/Library/Keychains/login.keychain-db" \
      "${HOME}/Library/Keychains/login.keychain"; do
    [[ -f "$k" ]] || continue
    if [[ "$k" == /Library/* ]]; then
      while sudo security delete-certificate -Z "$hash" "$k" 2>/dev/null; do :; done
      [[ -n "$cn" ]] && while sudo security delete-certificate -c "$cn" "$k" 2>/dev/null; do :; done
    else
      while security delete-certificate -Z "$hash" "$k" 2>/dev/null; do :; done
      [[ -n "$cn" ]] && while security delete-certificate -c "$cn" "$k" 2>/dev/null; do :; done
    fi
  done
}

trust_dashboard_cert_on_mac_if_needed() {
  [[ "${SKIP_MAC_TRUST:-0}" == "1" ]] && return 0
  [[ "$(uname -s)" == "Darwin" ]] || return 0

  local cert=""
  for p in "$SOURCE_CONFIG/dashboard.crt" "$SCRIPT_DIR/.certs/dashboard.crt"; do
    if [[ -f "$p" ]]; then cert="$p"; break; fi
  done
  [[ -n "$cert" ]] || return 0

  local hash cn k found
  hash=$(openssl x509 -in "$cert" -outform DER 2>/dev/null | shasum -a 256 | awk '{print $1}')
  [[ -n "$hash" ]] || return 0
  cn=$(openssl x509 -in "$cert" -noout -subject 2>/dev/null | sed -n 's/.*CN=\([^,;/]*\).*/\1/p' | head -1)

  # Scan keychains that actually exist on this Mac
  for k in \
      /Library/Keychains/System.keychain-db \
      /Library/Keychains/System.keychain \
      "${HOME}/Library/Keychains/login.keychain-db" \
      "${HOME}/Library/Keychains/login.keychain"; do
    [[ -f "$k" ]] || continue
    found=$(security find-certificate -a -Z "$hash" "$k" 2>/dev/null || true)
    if [[ -n "$found" ]]; then
      echo ">>> macOS: dashboard.crt already trusted (seen in $(basename "$k"))."
      return 0
    fi
  done

  echo ">>> macOS: removing previous dashboard.crt entries from keychains (if any)..."
  remove_dashboard_cert_from_mac_keychains "$cert" "$hash" "$cn"

  local errfile
  errfile=$(mktemp)
  echo ">>> macOS: importing dashboard.crt as trusted root..."
  for k in /Library/Keychains/System.keychain-db /Library/Keychains/System.keychain; do
    [[ -f "$k" ]] || continue
    : >"$errfile"
    # trustAsRoot → Keychain Access shows "Always Trust"; trustRoot leaves "Use System Defaults".
    if sudo security add-trusted-cert -d -r trustAsRoot -k "$k" "$cert" 2>"$errfile"; then
      echo ">>> macOS: trusted in System keychain ($(basename "$k"))."
      rm -f "$errfile"
      return 0
    fi
    if grep -qiE 'already exists|duplicate|SecDuplicateItem|The specified item already|SecDuplicateItemErr' "$errfile" 2>/dev/null; then
      echo ">>> macOS: certificate already in keychain."
      rm -f "$errfile"
      return 0
    fi
    # Keychain missing or other error — try next System path
    if ! grep -qi 'could not be found' "$errfile" 2>/dev/null; then
      cat "$errfile" >&2
    fi
  done

  # Fallback: user login keychain (no sudo; Safari/Chrome use it)
  for k in "${HOME}/Library/Keychains/login.keychain-db" "${HOME}/Library/Keychains/login.keychain"; do
    [[ -f "$k" ]] || continue
    : >"$errfile"
    if security add-trusted-cert -r trustAsRoot -k "$k" "$cert" 2>"$errfile"; then
      echo ">>> macOS: trusted in login keychain ($(basename "$k"))."
      rm -f "$errfile"
      return 0
    fi
    if grep -qiE 'already exists|duplicate|SecDuplicateItem|The specified item already' "$errfile" 2>/dev/null; then
      echo ">>> macOS: certificate already present."
      rm -f "$errfile"
      return 0
    fi
    cat "$errfile" >&2
    break
  done
  rm -f "$errfile"
  echo ">>> macOS: could not import cert — trust manually (Keychain Access)." >&2
}

maybe_generate_dashboard_tls

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

echo ">>> Remote: docker pull + compose recreate ($DOCKER)"
# shellcheck disable=SC2086
ssh "$SYNOLOGY_SSH" "${DOCKER} pull \"${IMAGE}\" && ${DOCKER} compose -f \"${REMOTE_BASE}/portainer-stack.yml\" pull inverter-dashboard && ${DOCKER} compose -f \"${REMOTE_BASE}/portainer-stack.yml\" up -d --force-recreate inverter-dashboard"

trust_dashboard_cert_on_mac_if_needed

echo ">>> Done. HTTPS if dashboard.crt + dashboard.key were deployed."
