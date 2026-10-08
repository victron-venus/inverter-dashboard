"""Validate and load one immutable snapshot of the configured server identity."""

import re
import ssl
import tempfile
from pathlib import Path

from .tls_policy import _certificate_key_ok, enforce_tls_minimum


def server_context(config, default_factory):
    """Retain Uvicorn's TLS options while rejecting undersized identity keys."""
    certificate = Path(config.ssl_certfile).read_bytes()
    key = Path(config.ssl_keyfile).read_bytes() if config.ssl_keyfile else None
    certificates = re.findall(
        rb"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", certificate, re.DOTALL
    )
    if not certificates:
        raise ssl.SSLError("TLS server identity requires a PEM certificate chain")
    try:
        for pem in certificates:
            der = ssl.PEM_cert_to_DER_cert(pem.decode("ascii"))
            if not _certificate_key_ok(der):
                raise ssl.SSLError("TLS server certificate key is below the security minimum")
    except ValueError:
        raise ssl.SSLError("TLS server identity contains a malformed certificate") from None

    # OpenSSL loads certificates and keys from paths. Use the validated snapshot,
    # so a concurrent replacement cannot substitute a different certificate.
    with tempfile.TemporaryDirectory(prefix="inverter-dashboard-tls-") as directory:
        with tempfile.NamedTemporaryFile(mode="wb", dir=directory, delete=False) as snapshot:
            snapshot.write(certificate)
            certificate_path = snapshot.name
        key_path = None
        if key is not None:
            with tempfile.NamedTemporaryFile(mode="wb", dir=directory, delete=False) as snapshot:
                snapshot.write(key)
                key_path = snapshot.name
        original = config.ssl_certfile, config.ssl_keyfile
        try:
            config.ssl_certfile = certificate_path
            config.ssl_keyfile = key_path
            context = default_factory()
        finally:
            config.ssl_certfile, config.ssl_keyfile = original
    return enforce_tls_minimum(context)
