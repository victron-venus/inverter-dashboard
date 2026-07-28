"""
Version management and self-update functionality
"""

import os
import sys
import logging
import aiofiles

import httpx


from .config import GITHUB_RAW_URL, SELF_UPDATE_ENABLED, UPDATE_PIN

logger = logging.getLogger(__name__)


class SelfUpdateDisabled(Exception):
    """Raised when self-update is attempted but not enabled."""


def get_version() -> str:
    """Read version from VERSION file — works whether run as script or frozen binary."""
    try:
        if getattr(sys, "frozen", False):
            # PyInstaller onefile: VERSION is extracted by bootloader to sys._MEIPASS
            version_path = os.path.join(sys._MEIPASS, "VERSION")
        else:
            version_path = os.path.join(os.path.dirname(__file__), "..", "..", "VERSION")
            version_path = os.path.normpath(version_path)
            if not os.path.isfile(version_path):
                # Fallback: look in package dir (pip install layout)
                version_path = os.path.join(os.path.dirname(__file__), "VERSION")
        with open(version_path, "r") as f:
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


async def download_and_update() -> tuple[bool, str]:
    """Download all Python modules from GitHub and update local files.

    Disabled by default (SELF_UPDATE_ENABLED must be true).
    When UPDATE_PIN is set, downloads from that tag/revision instead of main.
    """
    if not SELF_UPDATE_ENABLED:
        raise SelfUpdateDisabled("self-update is disabled (set SELF_UPDATE_ENABLED=true)")

    files_to_update = [
        "server.py",
        "config.py",
        "version.py",
        "mqtt_handler.py",
        "websocket_handler.py",
        "html_template.py",
        "ha_client.py",
        "local_config.example.py",
        "scripts/docker_healthcheck.py",
        "entrypoint.sh",
        "VERSION",
    ]

    try:
        new_version = "unknown"

        # PyInstaller onefile: use executable's directory for file writes,
        # not __file__ (points to transient _MEIPASS temp extraction).
        if getattr(sys, "frozen", False):
            import pathlib

            script_dir = pathlib.Path(sys.executable).parent.resolve()
        else:
            script_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))

        async with httpx.AsyncClient(timeout=30) as client:
            for filename in files_to_update:
                resp = await client.get(_update_url(filename))
                if resp.status_code != 200:
                    logger.warning("Failed to download %s: %s", filename, resp.status_code)
                    continue

                content = resp.text
                filepath = os.path.join(script_dir, filename)
                parent = os.path.dirname(filepath)
                if parent:
                    os.makedirs(parent, exist_ok=True)

                async with aiofiles.open(filepath, "w") as f:
                    await f.write(content)

                if filename == "VERSION":
                    new_version = content.strip()

                logger.info("Updated %s", filename)

        logger.info("Updated to v%s", new_version)
        return True, new_version

    except (OSError, IOError, httpx.HTTPError) as e:
        logger.exception("Update failed: %s", e)
        return False, str(e)
