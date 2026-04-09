"""
Configuration for Inverter Dashboard
"""

import os

# MQTT settings
MQTT_HOST = os.getenv('MQTT_HOST', '192.168.160.150')
MQTT_PORT = int(os.getenv('MQTT_PORT', '1883'))

# Web server settings
WEB_PORT = int(os.getenv('WEB_PORT', '8080'))

# GitHub repository for updates
GITHUB_REPO = "victron-venus/inverter-dashboard"
GITHUB_RAW_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main"
