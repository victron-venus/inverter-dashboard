#!/bin/bash
# Auto-update on startup: fetch latest code from GitHub, fallback to built-in

cd /app

echo "=== Inverter Dashboard starting ==="
echo "Built-in version: $(cat VERSION 2>/dev/null || echo 'unknown')"

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

echo "Starting server..."
exec python server.py "$@"
