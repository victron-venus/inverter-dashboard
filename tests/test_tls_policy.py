"""Real TLS requests against disposable PKI, never external services.

The loopback PKI/server helpers also exercise the Observability transport.
Only fixture contexts allow weak certificates; product contexts are unchanged.
"""

import http.client
import os
import select
import socket
import ssl
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from inverter_dashboard.tls_policy import enforce_peer_key_policy, httpx_client

Key = rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey
CHAIN_CASES = (
    "strong",
    "strong-ec",
    "weak-leaf",
    "weak-intermediate",
    "weak-root",
    "weak-2047-root",
    "weak-ec-root",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in os.environ:
        if name.upper().endswith("_PROXY") or name in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
            monkeypatch.delenv(name)


@pytest.mark.parametrize("version", [ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3])
@pytest.mark.parametrize("case", [*CHAIN_CASES, "untrusted", "wrong-host"])
@pytest.mark.parametrize("transport", ["httpx", "stdlib"])
async def test_verified_chain_before_application_data(
    chains, monkeypatch, version, case, transport
):
    chain = chains.get(case, chains["strong"])
    calibrate(chain, version)
    ca = chains["strong-ec"][2] if case == "untrusted" else chain[2]
    monkeypatch.setenv("SSL_CERT_FILE", str(ca))
    with peer(chain, version) as (port, observed):
        host = "127.0.0.1" if case == "wrong-host" else "localhost"
        if transport == "httpx":
            async with httpx_client() as client:
                if case.startswith("strong"):
                    response = await client.post(f"https://{host}:{port}", content=b"synthetic")
                    assert response.status_code == 200
                else:
                    with pytest.raises(httpx.ConnectError):
                        await client.post(f"https://{host}:{port}", content=b"synthetic")
        else:
            context = enforce_peer_key_policy(ssl.create_default_context())
            client = http.client.HTTPSConnection(host, port, context=context, timeout=5)
            try:
                if case.startswith("strong"):
                    client.request("POST", "/", body=b"synthetic")
                    assert client.getresponse().status == 200
                else:
                    with pytest.raises(ssl.SSLError):
                        client.request("POST", "/", body=b"synthetic")
            finally:
                client.close()
    assert bool(observed["application_bytes"]) == case.startswith("strong")


@pytest.mark.parametrize("case", ["strong", "weak-2047-root"])
@pytest.mark.parametrize("encrypted_proxy", [False, True])
async def test_proxy_tunnel_keeps_origin_policy(
    chains, monkeypatch, tmp_path, case, encrypted_proxy
):
    roots = tmp_path / "roots.pem"
    roots.write_bytes(chains["strong"][2].read_bytes() + chains[case][2].read_bytes())
    monkeypatch.setenv("SSL_CERT_FILE", str(roots))
    with (
        peer(chains[case], ssl.TLSVersion.TLSv1_3) as (port, observed),
        proxy(port, chains["strong"] if encrypted_proxy else None) as (proxy_port, requests),
    ):
        scheme = "https" if encrypted_proxy else "http"
        monkeypatch.setenv("HTTPS_PROXY", f"{scheme}://localhost:{proxy_port}")
        async with httpx_client() as client:
            if case == "strong":
                response = await client.post(f"https://localhost:{port}", content=b"synthetic")
                assert response.status_code == 200
            else:
                with pytest.raises(httpx.ConnectError):
                    await client.post(f"https://localhost:{port}", content=b"synthetic")
    assert len(requests) == 1
    assert bool(observed["application_bytes"]) == (case == "strong")


async def test_weak_https_proxy_gets_no_connect_or_proxy_authorization(chains, monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", str(chains["weak-2047-root"][2]))
    with proxy(1, chains["weak-2047-root"]) as (port, requests):
        monkeypatch.setenv("HTTPS_PROXY", f"https://synthetic:synthetic@localhost:{port}")
        async with httpx_client() as client:
            with pytest.raises(httpx.ConnectError):
                await client.post("https://localhost:1", content=b"must-not-leave")
    assert requests == []


@pytest.mark.parametrize("no_proxy", ["", "*", "localhost,.example.com,127.0.0.1,::1"])
async def test_environment_routing_matches_httpx(monkeypatch, no_proxy):
    monkeypatch.setenv("HTTP_PROXY", "http://proxy-http.invalid:8001")
    monkeypatch.setenv("HTTPS_PROXY", "https://proxy-https.invalid:8002")
    monkeypatch.setenv("ALL_PROXY", "http://proxy-all.invalid:8003")
    monkeypatch.setenv("NO_PROXY", no_proxy)
    async with httpx.AsyncClient() as baseline, httpx_client() as guarded:
        for url in (
            "http://example.com",
            "https://sub.example.com",
            "https://other.invalid",
            "http://localhost",
            "https://127.0.0.1",
            "https://[::1]",
        ):
            expected = baseline._transport_for_url(httpx.URL(url))._pool
            actual = guarded._transport_for_url(httpx.URL(url))._pool
            assert type(actual) is type(expected)
            assert getattr(actual, "_proxy_url", None) == getattr(expected, "_proxy_url", None)


def certificate(
    key: Key, name: str, issuer: x509.Certificate | None, issuer_key: Key, *, ca: bool
) -> x509.Certificate:
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer.subject if issuer else subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(hours=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=not ca,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=ca,
                crl_sign=ca,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), False
        )
    )
    if not ca:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]), False
        ).add_extension(
            x509.ExtendedKeyUsage(
                [ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]
            ),
            False,
        )
    return builder.sign(issuer_key, hashes.SHA256())


@pytest.fixture(scope="module")
def chains(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[Path, Path, Path]]:
    directory = tmp_path_factory.mktemp("otlp-synthetic-pki")
    result = {}
    for case in CHAIN_CASES:
        root_bits = {"weak-root": 1024, "weak-2047-root": 2047}.get(case, 2048)
        root_key: Key
        if case in ("strong-ec", "weak-ec-root"):
            curve = ec.SECP192R1() if case == "weak-ec-root" else ec.SECP256R1()
            root_key = ec.generate_private_key(curve)
        else:
            root_key = rsa.generate_private_key(65537, root_bits)
        root = certificate(root_key, case, None, root_key, ca=True)
        issuer, issuer_key = root, root_key
        intermediate = b""
        if case == "weak-intermediate":
            # Deliberately weak, disposable certificate for rejection testing.
            issuer_key = rsa.generate_private_key(65537, 1024)  # nosec B505
            issuer = certificate(issuer_key, "intermediate", root, root_key, ca=True)
            intermediate = issuer.public_bytes(serialization.Encoding.PEM)
        leaf_key: Key = (
            ec.generate_private_key(ec.SECP256R1())
            if case == "strong-ec"
            else rsa.generate_private_key(65537, 1024 if case == "weak-leaf" else 2048)
        )
        leaf = certificate(leaf_key, "localhost", issuer, issuer_key, ca=False)
        cert_file, key_file, ca_file = (
            directory / f"{case}.{suffix}" for suffix in ("pem", "key", "ca")
        )
        cert_file.write_bytes(leaf.public_bytes(serialization.Encoding.PEM) + intermediate)
        key_file.write_bytes(
            leaf_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        ca_file.write_bytes(root.public_bytes(serialization.Encoding.PEM))
        result[case] = cert_file, key_file, ca_file
    return result


@contextmanager
def peer(
    chain: tuple[Path, Path, Path],
    version: ssl.TLSVersion,
    *,
    redirect: str | None = None,
    client_auth: bool = False,
) -> Iterator[tuple[int, dict[str, Any]]]:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = context.maximum_version = version
    context.set_ciphers("DEFAULT:@SECLEVEL=0")
    context.load_cert_chain(chain[0], chain[1])
    if client_auth:
        context.load_verify_locations(chain[2])
        context.verify_mode = ssl.CERT_REQUIRED
    result: dict[str, Any] = {"application_bytes": b""}
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)

        def worker() -> None:
            try:
                raw, _ = listener.accept()
                with raw:
                    raw.settimeout(5)
                    with context.wrap_socket(raw, server_side=True) as connection:
                        result["tls"] = connection.version()
                        if client_auth:
                            result["client_certificate"] = connection.getpeercert(binary_form=True)
                        data = b""
                        while b"\r\n\r\n" not in data:
                            part = connection.recv(8192)
                            if not part:
                                if not data:
                                    result["closed_before_http"] = True
                                    return
                                raise EOFError("Expected HTTP headers")
                            data += part
                            result["application_bytes"] = data
                        headers, body = data.split(b"\r\n\r\n", 1)
                        length = next(
                            int(line.split(b":", 1)[1])
                            for line in headers.split(b"\r\n")
                            if line.lower().startswith(b"content-length:")
                        )
                        while len(body) < length:
                            part = connection.recv(8192)
                            if not part:
                                raise EOFError("Expected complete OTLP body")
                            body += part
                        result["application_bytes"] = headers + b"\r\n\r\n" + body
                        response = (
                            f"HTTP/1.1 307 Temporary Redirect\r\nLocation: {redirect}\r\n"
                            if redirect
                            else "HTTP/1.1 200 OK\r\n"
                        )
                        connection.sendall(
                            (response + "Content-Length: 0\r\nConnection: close\r\n\r\n").encode()
                        )
            except ssl.SSLError as error:
                result["handshake_error"] = str(error)
            except ConnectionResetError as error:
                if result["application_bytes"]:
                    result["unexpected_error"] = repr(error)
                else:
                    result["closed_before_http"] = True
            except Exception as error:
                result["unexpected_error"] = repr(error)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            yield listener.getsockname()[1], result
        finally:
            thread.join(6)
            assert not thread.is_alive()
            assert "unexpected_error" not in result, result


def calibrate(chain: tuple[Path, Path, Path], version: ssl.TLSVersion) -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.set_ciphers("DEFAULT:@SECLEVEL=0")
    context.load_verify_locations(chain[2])
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    with peer(chain, version) as (port, observed):
        connection = http.client.HTTPSConnection("localhost", port, context=context, timeout=5)
        try:
            connection.request("POST", "/oracle", body=b"calibration")
            assert connection.getresponse().status == 200
        finally:
            connection.close()
    assert observed["application_bytes"].endswith(b"calibration")


@contextmanager
def proxy(
    target_port: int, chain: tuple[Path, Path, Path] | None
) -> Iterator[tuple[int, list[bytes]]]:
    """A bounded CONNECT relay whose only permitted destination is our loopback peer."""
    observed: list[bytes] = []
    errors: list[Exception] = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)

        def worker() -> None:
            try:
                raw, _ = listener.accept()
                with raw:
                    raw.settimeout(5)
                    if chain:
                        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                        context.set_ciphers("DEFAULT:@SECLEVEL=0")
                        context.load_cert_chain(chain[0], chain[1])
                        incoming: socket.socket = context.wrap_socket(raw, server_side=True)
                    else:
                        incoming = raw
                    with incoming:
                        request = b""
                        while b"\r\n\r\n" not in request:
                            data = incoming.recv(8192)
                            if not data:
                                return
                            request += data
                        observed.append(request)
                        assert request.startswith(f"CONNECT localhost:{target_port} ".encode())
                        with socket.create_connection(("127.0.0.1", target_port), timeout=5) as out:
                            incoming.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                            while True:
                                ready, _, _ = select.select([incoming, out], [], [], 5)
                                if not ready:
                                    raise TimeoutError("Synthetic proxy did not finish")
                                for source in ready:
                                    data = source.recv(8192)
                                    if not data:
                                        return
                                    destination = out if source is incoming else incoming
                                    destination.sendall(data)
            except ssl.SSLError:
                # TLS clients may reject this proxy certificate or close its tunnel.
                pass
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            yield listener.getsockname()[1], observed
        finally:
            thread.join(6)
            assert not thread.is_alive()
            assert not errors, errors
