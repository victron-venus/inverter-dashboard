"""
Configuration for Inverter Dashboard
"""

import os

# MQTT settings
MQTT_HOST = os.getenv("MQTT_HOST", "Cerbo")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))

# Web server settings
HOST = os.getenv("HOST", "127.0.0.1")  # Default to localhost for security
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))

# Dashboard authentication — REQUIRED. Set via DASHBOARD_SECRET env var.
# Protects WebSocket commands and /api/* management endpoints.
# Generate with: python3 -c "import secrets; print(secrets.token_urlsafe(32))"
DASHBOARD_SECRET = os.getenv("DASHBOARD_SECRET", "")

# Self-update settings
SELF_UPDATE_ENABLED = os.getenv("SELF_UPDATE_ENABLED", "false").lower() in ("1", "true", "yes")
# Pin updates to a specific git tag/revision instead of main (empty = use GITHUB_RAW_URL default)
UPDATE_PIN = os.getenv("UPDATE_PIN", "")

# MQTT authentication (optional)
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")
MQTT_TLS = os.getenv("MQTT_TLS", "").lower() in ("1", "true", "yes")
MQTT_CA_CERT = os.getenv("MQTT_CA_CERT", "")

# Default inverter limits (used by websocket_handler)
DEFAULT_POWER_MIN = -2300
DEFAULT_POWER_MAX = 2250
DEFAULT_LOOP_INTERVAL = 0.33

# Console lines kept in memory
CONSOLE_MAX_LINES = 50
CONSOLE_SEND_LINES = 20

# HA poll timeout
HA_POLL_TIMEOUT = 20.0
HA_REQUEST_TIMEOUT = 15.0

# GitHub repository for updates
GITHUB_REPO = "victron-venus/inverter-dashboard"
GITHUB_RAW_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main"
