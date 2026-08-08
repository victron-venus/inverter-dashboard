"""
Configuration for Inverter Dashboard using pydantic-settings
"""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """Application configuration loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # MQTT settings
    MQTT_HOST: str = "Cerbo"
    MQTT_PORT: int = 1883
    MQTT_USERNAME: str = ""
    MQTT_PASSWORD: str = ""
    MQTT_TLS: bool = False
    MQTT_CA_CERT: str = ""

    # Web server settings
    HOST: str = "127.0.0.1"
    WEB_PORT: int = 8080

    # Dashboard authentication — REQUIRED. Set via DASHBOARD_SECRET env var.
    # Protects WebSocket commands and /api/* management endpoints.
    # Generate with: python3 -c "import secrets; print(secrets.token_urlsafe(32))"
    DASHBOARD_SECRET: str = ""

    # Self-update settings
    SELF_UPDATE_ENABLED: bool = False
    UPDATE_PIN: str = ""

    # Default inverter limits (used by websocket_handler)
    DEFAULT_POWER_MIN: int = -2300
    DEFAULT_POWER_MAX: int = 2250
    DEFAULT_LOOP_INTERVAL: float = 0.33

    # Console lines kept in memory
    CONSOLE_MAX_LINES: int = 50
    CONSOLE_SEND_LINES: int = 20

    # HA poll timeout
    HA_POLL_TIMEOUT: float = 20.0
    HA_REQUEST_TIMEOUT: float = 15.0

    # GitHub repository for updates
    GITHUB_REPO: str = "victron-venus/inverter-dashboard"

    @property
    def GITHUB_RAW_URL(self) -> str:
        return f"https://raw.githubusercontent.com/{self.GITHUB_REPO}/main"

    @field_validator("SELF_UPDATE_ENABLED", mode="before")
    @classmethod
    def _parse_bool(cls, v: str | bool) -> bool:
        if isinstance(v, bool):
            return v
        return v.lower() in ("1", "true", "yes")

    @field_validator("MQTT_TLS", mode="before")
    @classmethod
    def _parse_mqtt_tls(cls, v: str | bool) -> bool:
        if isinstance(v, bool):
            return v
        return v.lower() in ("1", "true", "yes")


config = Config()

# Module-level exports for backward compatibility
MQTT_HOST = config.MQTT_HOST
MQTT_PORT = config.MQTT_PORT
MQTT_USERNAME = config.MQTT_USERNAME
MQTT_PASSWORD = config.MQTT_PASSWORD
MQTT_TLS = config.MQTT_TLS
MQTT_CA_CERT = config.MQTT_CA_CERT
HOST = config.HOST
WEB_PORT = config.WEB_PORT
DASHBOARD_SECRET = config.DASHBOARD_SECRET
SELF_UPDATE_ENABLED = config.SELF_UPDATE_ENABLED
UPDATE_PIN = config.UPDATE_PIN
DEFAULT_POWER_MIN = config.DEFAULT_POWER_MIN
DEFAULT_POWER_MAX = config.DEFAULT_POWER_MAX
DEFAULT_LOOP_INTERVAL = config.DEFAULT_LOOP_INTERVAL
CONSOLE_MAX_LINES = config.CONSOLE_MAX_LINES
CONSOLE_SEND_LINES = config.CONSOLE_SEND_LINES
HA_POLL_TIMEOUT = config.HA_POLL_TIMEOUT
HA_REQUEST_TIMEOUT = config.HA_REQUEST_TIMEOUT
GITHUB_REPO = config.GITHUB_REPO
GITHUB_RAW_URL = config.GITHUB_RAW_URL
