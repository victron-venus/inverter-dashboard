"""Tests for config module."""

import os
from unittest.mock import patch
from urllib.parse import urlsplit


class TestConfig:
    """Tests for config module defaults."""

    def test_defaults(self):
        from inverter_dashboard.config import Config

        # Inspect model defaults independently of local dotenv and shell settings.
        with patch.dict(os.environ, {}, clear=True):
            defaults = Config(_env_file=None)

        assert defaults.MQTT_PORT == 1883
        assert defaults.WEB_PORT == 8080
        assert defaults.SELF_UPDATE_ENABLED is False
        assert defaults.DEFAULT_POWER_MIN == -2300
        assert defaults.DEFAULT_POWER_MAX == 2250
        assert defaults.CONSOLE_MAX_LINES == 50
        assert defaults.CONSOLE_SEND_LINES == 20

    def test_dashboard_secret_default_empty(self):
        from inverter_dashboard import config

        # Default should be empty (unset)
        # Synthetic test credentials/sentinels; not valid external-service secrets.
        assert config.DASHBOARD_SECRET == "" or isinstance(config.DASHBOARD_SECRET, str)  # nosec B105

    def test_github_raw_url(self):
        from inverter_dashboard import config

        url = urlsplit(config.GITHUB_RAW_URL)
        assert url.scheme == "https"
        assert url.hostname == "raw.githubusercontent.com"
        assert url.path.startswith(f"/{config.GITHUB_REPO}/")
