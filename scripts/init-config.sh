#!/usr/bin/env bash
# Create config/ha_secrets.py from example (not tracked — gitignored). Run once after clone.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/config"
EX_SRC="$ROOT/ha_secrets.example.py"
DST="$ROOT/config/ha_secrets.py"
if [[ ! -f "$EX_SRC" ]]; then
  echo "Missing $EX_SRC" >&2
  exit 1
fi
if [[ -f "$DST" ]]; then
  echo "Already exists: $DST"
  exit 0
fi
cp "$EX_SRC" "$DST"
chmod 600 "$DST" 2>/dev/null || true
echo "Created $DST — edit HA_TOKEN and entities, then restart the dashboard."
