"""
Version management and self-update functionality
"""

import os
import sys
import logging

import httpx


from .config import GITHUB_RAW_URL, SELF_UPDATE_ENABLED, UPDATE_PIN

logger = logging.getLogger(__name__)


class SelfUpdateDisabled(Exception):
    """Raised when self-update is attempted but not enabled."""


def get_version() -> str:
    """Read version from VERSION file — works whether run as script or frozen binary."""
    try:
        if getattr(sys, "frozen", False):
            version_path = os.path.join(sys._MEIPASS, "VERSION")  # pylint: disable=protected-access
        else:
            version_path = os.path.normpath(
                os.path.join(os.path.dirname(__file__), "..", "..", "VERSION")
            )
            if not os.path.isfile(version_path):
                version_path = os.path.join(os.path.dirname(__file__), "VERSION")
        with open(version_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except (OSError, IOError):
        return "dev"


VERSION = get_version()


def _update_url(filename: str) -> str:
    """Build URL for a given file, respecting UPDATE_PIN."""
    if UPDATE_PIN:
        return f"https://raw.githubusercontent.com/victron-venus/inverter-dashboard/{UPDATE_PIN}/{filename}"
    return f"{GITHUB_RAW_URL}/{filename}"


async def check_latest_version() -> str | None:
    """Check GitHub for latest version from VERSION file on main branch"""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(_update_url("VERSION"), timeout=3.0)
            if resp.status_code == 200:
                latest = resp.text.strip()
                logger.info("Latest version: %s, current: %s", latest, VERSION)
                return latest
    except httpx.HTTPError as e:
        logger.warning("Failed to check latest version: %s", e)
    return None


def download_and_update() -> tuple[bool, str]:
    """Self-update disabled by default. Enable with SELF_UPDATE_ENABLED=true."""
    if not SELF_UPDATE_ENABLED:
        raise SelfUpdateDisabled("self-update is disabled (set SELF_UPDATE_ENABLED=true)")
    return False, "not implemented"
