#!/usr/bin/env python3
"""Docker HEALTHCHECK: HTTP or HTTPS when dashboard.crt + dashboard.key exist in config.

Note: CERT_NONE is used intentionally for self-signed localhost certs — the healthcheck
runs inside the container, so there is no man-in-the-middle risk on 127.0.0.1.
"""
from __future__ import annotations

import os
import ssl
import sys
import urllib.request


def main() -> int:
    config = os.environ.get("INVERTER_DASHBOARD_CONFIG", "/app/config")
    port = os.environ.get("WEB_PORT", "8080")
    crt = os.path.join(config, "dashboard.crt")
    key = os.path.join(config, "dashboard.key")
    host = f"127.0.0.1:{port}"
    if os.path.isfile(crt) and os.path.isfile(key):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # Intentional: self-signed localhost cert inside container
        url = f"https://{host}/api/state"
        urllib.request.urlopen(url, context=ctx, timeout=8)
        return 0
    url = f"http://{host}/api/state"
    urllib.request.urlopen(url, timeout=8)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        raise SystemExit(1)
