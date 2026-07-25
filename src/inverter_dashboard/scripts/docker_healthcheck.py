#!/usr/bin/env python3
"""Docker HEALTHCHECK: HTTP or HTTPS when dashboard.crt + dashboard.key exist in config."""

from __future__ import annotations

import os
import ssl
import urllib.request
import urllib.error


def main() -> int:
    config = os.environ.get("INVERTER_DASHBOARD_CONFIG", "/app/config")
    port = os.environ.get("WEB_PORT", "8080")
    crt = os.path.join(config, "dashboard.crt")
    key = os.path.join(config, "dashboard.key")
    host = f"127.0.0.1:{port}"
    # Localhost-only container healthcheck; HTTPS is used below when TLS is configured.
    url = f"http://{host}/api/state"  # NOSONAR
    timeout = 8

    try:
        if os.path.isfile(crt) and os.path.isfile(key):
            # For HTTPS with self-signed certs inside container at localhost:
            # Use secure context with hostname verification disabled only for localhost.
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False  # NOSONAR
            ctx.verify_mode = ssl.CERT_NONE  # NOSONAR
            url = f"https://{host}/api/state"
            urllib.request.urlopen(url, context=ctx, timeout=timeout)
        else:
            urllib.request.urlopen(url, timeout=timeout)
        return 0
    except urllib.error.HTTPError as e:
        # 401/403 means server is up but auth failed - health is OK
        if e.code in (401, 403):
            return 0
        return 1
    except (urllib.error.URLError, OSError, ssl.SSLError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
