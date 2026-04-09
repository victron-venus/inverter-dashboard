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
    """Check GitHub for latest release version"""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                timeout=10.0
            )
            if resp.status_code == 200:
                data = resp.json()
                latest = data.get('tag_name', '').lstrip('v')
                logger.info(f"Latest version: {latest}, current: {VERSION}")
                return latest
    except Exception as e:
        logger.warning(f"Failed to check latest version: {e}")
    return None


async def download_and_update() -> tuple[bool, str]:
    """Download latest server.py from GitHub and update local files"""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Download new server.py
            resp = await client.get(f"{GITHUB_RAW_URL}/server.py")
            if resp.status_code != 200:
                return False, f"Failed to download server.py: {resp.status_code}"
            new_code = resp.text
            
            # Download new VERSION
            ver_resp = await client.get(f"{GITHUB_RAW_URL}/VERSION")
            new_version = ver_resp.text.strip() if ver_resp.status_code == 200 else "unknown"
        
        # Write new server.py
        script_dir = os.path.dirname(__file__)
        server_path = os.path.join(script_dir, 'server.py')
        with open(server_path, 'w') as f:
            f.write(new_code)
        
        # Write new VERSION
        version_path = os.path.join(script_dir, 'VERSION')
        with open(version_path, 'w') as f:
            f.write(new_version + '\n')
        
        logger.info(f"Updated to v{new_version}")
        return True, new_version
        
    except Exception as e:
        logger.error(f"Update failed: {e}")
        return False, str(e)
