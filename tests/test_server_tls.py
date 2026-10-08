"""Server identity snapshots and loopback health probes use exact key minima."""

import http.client
import ipaddress
import os
import socket
import ssl
import stat
import sys
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from inverter_dashboard import server
from inverter_dashboard.scripts import docker_healthcheck
from inverter_dashboard.server_tls import server_context


@pytest.fixture(scope="module")
def identities(tmp_path_factory):
    directory = tmp_path_factory.mktemp("server-tls")
    identities = {}
    for bits in (1024, 2047, 2048):
        # Disposable weak private keys are deliberate rejection fixtures.
        key = rsa.generate_private_key(65537, bits)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        now = datetime.now(UTC)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(hours=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
                ),
                False,
            )
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
            .sign(key, hashes.SHA256())
        )
        certfile, keyfile = directory / f"{bits}.crt", directory / f"{bits}.key"
        certfile.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        keyfile.touch(mode=0o600)
        keyfile.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        identities[bits] = (certfile, keyfile)
    return identities


def configuration(identity, factory=server_context):
    return uvicorn.Config(
        server.app,
        ssl_certfile=str(identity[0]),
        ssl_keyfile=str(identity[1]),
        ssl_context_factory=factory,
    )


@contextmanager
def https_peer(context):
    observed = {"request": b""}
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)

        def receive():
            try:
                raw, _ = listener.accept()
                with raw:
                    raw.settimeout(5)
                    with context.wrap_socket(raw, server_side=True) as connection:
                        while b"\r\n\r\n" not in observed["request"]:
                            part = connection.recv(4096)
                            if not part:
                                return
                            observed["request"] += part
                            if len(observed["request"]) > 16384:
                                raise ValueError("Synthetic request exceeded its bound")
                        connection.sendall(
                            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK"
                        )
            except (ssl.SSLError, ConnectionError):
                pass
            except Exception as error:
                observed["unexpected"] = repr(error)

        thread = threading.Thread(target=receive, daemon=True)
        thread.start()
        try:
            yield listener.getsockname()[1], observed
        finally:
            thread.join(6)
            assert not thread.is_alive()
            assert "unexpected" not in observed, observed


@pytest.mark.parametrize("bits", [1024, 2047])
def test_server_rejects_weak_identity_before_listener(identities, bits):
    config = configuration(identities[bits])
    with pytest.raises(ssl.SSLError, match="below the security minimum"):
        config.load()


def test_server_preserves_configured_cipher_allowlist(identities):
    expression = "ECDHE-RSA-AES128-GCM-SHA256:@SECLEVEL=1"
    original = configuration(identities[2048], factory=None)
    original.ssl_ciphers = expression
    original.load()
    guarded = configuration(identities[2048])
    guarded.ssl_ciphers = expression
    guarded.load()
    assert guarded.ssl.get_ciphers() == original.ssl.get_ciphers()
    assert guarded.ssl.security_level == 2


@pytest.mark.parametrize("version", [ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3])
@pytest.mark.parametrize("combined", [False, True])
def test_strong_identity_serves_tls(identities, tmp_path, version, combined):
    identity = identities[2048]
    config = configuration(identity)
    if combined:
        bundle = tmp_path / "combined.pem"
        bundle.touch(mode=0o600)
        bundle.write_bytes(identity[0].read_bytes() + identity[1].read_bytes())
        config.ssl_certfile, config.ssl_keyfile = str(bundle), None
    config.load()
    context = ssl.create_default_context(cafile=str(identity[0]))
    context.minimum_version = context.maximum_version = version
    with https_peer(config.ssl) as (port, observed):
        client = http.client.HTTPSConnection("localhost", port, context=context, timeout=5)
        try:
            client.request("GET", "/")
            assert client.getresponse().read() == b"OK"
        finally:
            client.close()
    assert observed["request"].startswith(b"GET / HTTP/")


def test_snapshot_prevents_replacement_and_cleans_private_files(identities, tmp_path):
    certificate, key = tmp_path / "identity.crt", tmp_path / "identity.key"
    certificate.write_bytes(identities[2048][0].read_bytes())
    key.write_bytes(identities[2048][1].read_bytes())
    config = configuration((certificate, key))
    captured = []

    def factory(config, default):
        def replace_originals_then_load():
            from pathlib import Path

            for path in (config.ssl_certfile, config.ssl_keyfile):
                captured.append(Path(path))
                assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
                assert stat.S_IMODE(os.stat(Path(path).parent).st_mode) == 0o700
            certificate.write_bytes(identities[2047][0].read_bytes())
            key.write_bytes(identities[2047][1].read_bytes())
            return default()

        return server_context(config, replace_originals_then_load)

    config.ssl_context_factory = factory
    config.load()
    assert (config.ssl_certfile, config.ssl_keyfile) == (str(certificate), str(key))
    assert all(not path.exists() for path in captured)
    context = ssl.create_default_context(cafile=str(identities[2048][0]))
    with https_peer(config.ssl) as (port, observed):
        client = http.client.HTTPSConnection("localhost", port, context=context, timeout=5)
        try:
            client.request("GET", "/")
            assert client.getresponse().status == 200
        finally:
            client.close()
    assert observed["request"]


def test_main_installs_identity_factory(monkeypatch, identities):
    captured = {}
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "dashboard",
            "--ssl-cert",
            str(identities[2048][0]),
            "--ssl-key",
            str(identities[2048][1]),
        ],
    )
    monkeypatch.setattr(server.uvicorn, "run", lambda app, **kwargs: captured.update(kwargs))
    server.main()
    assert captured["ssl_context_factory"] is server_context


def test_mismatched_key_restores_configuration_and_removes_snapshot(identities):
    from pathlib import Path

    config = configuration((identities[2048][0], identities[2047][1]))
    original = config.ssl_certfile, config.ssl_keyfile
    paths = []

    def factory(config, default):
        def fail_loading_mismatched_pair():
            paths.extend([Path(config.ssl_certfile), Path(config.ssl_keyfile)])
            return default()

        return server_context(config, fail_loading_mismatched_pair)

    config.ssl_context_factory = factory
    with pytest.raises(ssl.SSLError):
        config.load()
    assert (config.ssl_certfile, config.ssl_keyfile) == original
    assert paths and all(not path.exists() for path in paths)


@pytest.mark.parametrize("version", [ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3])
@pytest.mark.parametrize("bits", [2047, 2048])
def test_health_probe_checks_its_actual_tls_peer(identities, tmp_path, monkeypatch, version, bits):
    identity = identities[bits]
    (tmp_path / "dashboard.crt").write_bytes(identity[0].read_bytes())
    (tmp_path / "dashboard.key").touch()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = context.maximum_version = version
    context.set_ciphers("DEFAULT:@SECLEVEL=0")
    context.load_cert_chain(*identity)
    monkeypatch.setenv("INVERTER_DASHBOARD_CONFIG", str(tmp_path))
    with https_peer(context) as (port, observed):
        monkeypatch.setenv("WEB_PORT", str(port))
        assert docker_healthcheck.main() == (0 if bits == 2048 else 1)
    assert bool(observed["request"]) == (bits == 2048)
