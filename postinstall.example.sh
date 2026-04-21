#!/usr/bin/env bash
# Deprecated wrapper: use the checked-in **postinstall.sh** in the repo root.
# Kept so old links still work — forwards to real script.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec bash "$SCRIPT_DIR/postinstall.sh" "$@"
