#!/usr/bin/env python3
"""Docker HEALTHCHECK: HTTP or HTTPS when dashboard.crt + dashboard.key exist in config."""

from __future__ import annotations

import os
import ssl
import urllib.request


def main() -> int:
    config = os.environ.get("INVERTER_DASHBOARD_CONFIG", "/app/config")
    port = os.environ.get("WEB_PORT", "8080")
    crt = os.path.join(config, "dashboard.crt")
    key = os.path.join(config, "dashboard.key")
    host = f"127.0.0.1:{port}"
    url = f"http://{host}/api/state"
    if os.path.isfile(crt) and os.path.isfile(key):
        # For HTTPS with self-signed certs inside container at localhost:
        # trust the system CA store but allow localhost IP without full verification.
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # nosec: localhost-only healthcheck
        url = f"https://{host}/api/state"
        urllib.request.urlopen(url, context=ctx, timeout=8)
        return 0
    urllib.request.urlopen(url, timeout=8)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
