"""
Version management and self-update functionality
"""

import os
import logging
import httpx

from config import GITHUB_REPO, GITHUB_RAW_URL

logger = logging.getLogger(__name__)


def get_version() -> str:
    """Read version from VERSION file"""
    try:
        version_path = os.path.join(os.path.dirname(__file__), 'VERSION')
        with open(version_path, 'r') as f:
            return f.read().strip()
    except:
        return 'dev'


VERSION = get_version()


async def check_latest_version() -> str | None:
    """Check GitHub for latest version from VERSION file on main branch"""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{GITHUB_RAW_URL}/VERSION",
                timeout=10.0
            )
            if resp.status_code == 200:
                latest = resp.text.strip()
                logger.info(f"Latest version: {latest}, current: {VERSION}")
                return latest
    except Exception as e:
        logger.warning(f"Failed to check latest version: {e}")
    return None


async def download_and_update() -> tuple[bool, str]:
    """Download all Python modules from GitHub and update local files"""
    files_to_update = [
        'server.py',
        'config.py',
        'version.py',
        'mqtt_handler.py',
        'websocket_handler.py',
        'html_template.py',
        'ha_client.py',
        'ha_secrets.example.py',
        'VERSION',
    ]
    
    try:
        script_dir = os.path.dirname(__file__)
        new_version = "unknown"
        
        async with httpx.AsyncClient(timeout=30) as client:
            for filename in files_to_update:
                resp = await client.get(f"{GITHUB_RAW_URL}/{filename}")
                if resp.status_code != 200:
                    logger.warning(f"Failed to download {filename}: {resp.status_code}")
                    continue
                
                content = resp.text
                filepath = os.path.join(script_dir, filename)
                
                with open(filepath, 'w') as f:
                    f.write(content)
                
                if filename == 'VERSION':
                    new_version = content.strip()
                
                logger.info(f"Updated {filename}")
        
        logger.info(f"Updated to v{new_version}")
        return True, new_version
        
    except Exception as e:
        logger.error(f"Update failed: {e}")
        return False, str(e)
