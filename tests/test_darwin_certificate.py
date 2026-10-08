"""Native DER key-size boundaries and cleanup, without trust-store changes."""

import asyncio
import ctypes
import http.client
import importlib.util
import os
import platform
import shutil
import socket
import ssl
import subprocess  # nosec B404 -- fixed OpenSSL argv for disposable certificate fixtures.
import sys
import threading
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from inverter_dashboard import _darwin_certificate as policy


@pytest.fixture(scope="module")
def native_pki(tmp_path_factory):
    if sys.platform != "darwin":
        pytest.skip("Apple Security framework is available only on macOS")
    openssl = os.environ.get("DARWIN_CERTIFICATE_OPENSSL") or shutil.which("openssl")
    assert openssl, "openssl is required to generate ephemeral native certificate fixtures"
    folder = tmp_path_factory.mktemp("darwin-certificate")
    result = {}
    for name, algorithm, options in (
        ("rsa1024", "RSA", ["rsa_keygen_bits:1024"]),
        ("rsa2047", "RSA", ["rsa_keygen_bits:2047"]),
        ("rsa2048", "RSA", ["rsa_keygen_bits:2048"]),
        ("rsa4096", "RSA", ["rsa_keygen_bits:4096"]),
        ("ec192", "EC", ["ec_paramgen_curve:prime192v1"]),
        ("ec224", "EC", ["ec_paramgen_curve:secp224r1"]),
        ("ec256", "EC", ["ec_paramgen_curve:prime256v1"]),
        ("ec384", "EC", ["ec_paramgen_curve:secp384r1"]),
    ):
        key = folder / f"{name}.key"
        command = [openssl, "genpkey", "-algorithm", algorithm, "-out", str(key)]
        for option in options:
            command.extend(["-pkeyopt", option])
        subprocess.run(command, check=True, capture_output=True, timeout=30)  # nosec B603 -- trusted OpenSSL; no shell.
        key.chmod(0o600)
        der = folder / f"{name}.der"
        subprocess.run(  # nosec B603 -- trusted test-runner OpenSSL, fixed argv; no shell.
            [
                openssl,
                "req",
                "-new",
                "-x509",
                "-key",
                str(key),
                "-outform",
                "DER",
                "-out",
                str(der),
                "-days",
                "1",
                "-sha256",
                "-subj",
                "/CN=ephemeral.invalid",
                "-addext",
                "basicConstraints=critical,CA:TRUE",
                "-addext",
                "keyUsage=critical,keyCertSign,cRLSign",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        result[name] = der.read_bytes()
    return folder, openssl, result


@pytest.fixture(scope="module")
def certificates(native_pki):
    return native_pki[2]


@pytest.fixture(scope="module")
def tls_certificates(native_pki):
    folder, openssl, certificates = native_pki
    key = folder / "leaf.key"
    subprocess.run(  # nosec B603 -- trusted test-runner OpenSSL, fixed argv; no shell.
        [
            openssl,
            "genpkey",
            "-algorithm",
            "RSA",
            "-pkeyopt",
            "rsa_keygen_bits:2048",
            "-out",
            str(key),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    key.chmod(0o600)
    csr = folder / "leaf.csr"
    subprocess.run(  # nosec B603 -- trusted test-runner OpenSSL, fixed argv; no shell.
        [openssl, "req", "-new", "-key", str(key), "-out", str(csr), "-subj", "/CN=localhost"],
        check=True,
        capture_output=True,
        timeout=30,
    )
    for name, der in certificates.items():
        (folder / f"{name}.ca").write_text(ssl.DER_cert_to_PEM_cert(der))
    for name in ("rsa1024", "rsa2047", "rsa2048", "ec192", "ec224", "ec256", "wronghost"):
        root = "rsa2048" if name == "wronghost" else name
        dns = "wrong.invalid" if name == "wronghost" else "localhost"
        extensions = folder / f"{name}.extensions"
        extensions.write_text(
            "basicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature,keyEncipherment\n"
            "extendedKeyUsage=serverAuth\n"
            "subjectKeyIdentifier=hash\n"
            "authorityKeyIdentifier=keyid,issuer\n"
            f"subjectAltName=DNS:{dns}\n"
        )
        subprocess.run(  # nosec B603 -- trusted test-runner OpenSSL, fixed argv; no shell.
            [
                openssl,
                "x509",
                "-req",
                "-in",
                str(csr),
                "-CA",
                str(folder / f"{root}.ca"),
                "-CAkey",
                str(folder / f"{root}.key"),
                "-set_serial",
                "2",
                "-days",
                "1",
                "-sha256",
                "-extfile",
                str(extensions),
                "-out",
                str(folder / f"{name}.pem"),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
    return folder


@contextmanager
def tls_server(folder, case, version):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = context.maximum_version = version
    # Only the disposable server permits weak chains so rejection is tested in the client.
    context.set_ciphers("DEFAULT:@SECLEVEL=0")
    context.load_cert_chain(folder / f"{case}.pem", folder / "leaf.key")
    observed = {"application": b""}
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
                        while b"\r\n\r\n" not in observed["application"]:
                            data = connection.recv(8192)
                            if not data:
                                return
                            observed["application"] += data
                            if len(observed["application"]) > 16384:
                                raise ValueError("Synthetic request exceeded its bound")
                        connection.sendall(
                            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK"
                        )
            except (ssl.SSLError, ConnectionResetError) as error:
                observed["rejection"] = type(error).__name__
            except Exception as error:
                observed["unexpected"] = repr(error)

        thread = threading.Thread(target=receive, daemon=True)
        thread.start()
        try:
            yield listener.getsockname()[1], observed
        finally:
            thread.join(6)
            assert not thread.is_alive(), "Synthetic TLS server did not stop"
            assert "unexpected" not in observed, observed


def tls_request(kind, context, port):
    if kind == "stdlib":
        client = http.client.HTTPSConnection("localhost", port, context=context, timeout=5)
        try:
            client.request("GET", "/", headers={"Authorization": "Bearer synthetic-darwin-test"})
            assert client.getresponse().read() == b"OK"
        finally:
            client.close()
    else:
        import httpx

        async def request():
            async with httpx.AsyncClient(verify=context, trust_env=False, timeout=5) as client:
                result = await client.get(
                    f"https://localhost:{port}/",
                    headers={"Authorization": "Bearer synthetic-darwin-test"},
                )
                assert result.status_code == 200 and result.content == b"OK"

        asyncio.run(request())


@pytest.mark.parametrize("kind", ["stdlib", "httpx"])
@pytest.mark.parametrize("version", [ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3])
@pytest.mark.parametrize(
    "case", ["rsa1024", "rsa2047", "rsa2048", "ec192", "ec224", "ec256", "wronghost", "untrusted"]
)
def test_native_policy_before_http(
    monkeypatch, tls_certificates, certificates, kind, version, case
):
    import httpx

    from inverter_dashboard import tls_policy

    folder = tls_certificates
    offered = "rsa2048" if case == "untrusted" else case
    root = "rsa2048" if case == "wronghost" else offered
    trusted = "rsa4096" if case == "untrusted" else root
    if case not in ("wronghost", "untrusted"):
        # Independent CA/hostname verification first: the weak fixture must be usable.
        oracle = ssl.create_default_context(cafile=str(folder / f"{root}.ca"))
        oracle.set_ciphers("DEFAULT:@SECLEVEL=0")
        with tls_server(folder, offered, version) as (port, observed):
            tls_request(kind, oracle, port)
        assert b"Bearer synthetic-darwin-test" in observed["application"]

    examined = []
    backend = (
        tls_policy._certificate_key_ok
        if platform.machine() == "x86_64"
        else policy.certificate_key_meets_minimum
    )

    def inspect_native(der):
        examined.append(der)
        return backend(der)

    # Intel exercises production dispatch. ARM selects the native backend explicitly;
    # its result is not an Intel runtime claim.
    monkeypatch.setattr(tls_policy, "_certificate_key_ok", inspect_native)
    context = ssl.create_default_context(cafile=str(folder / f"{trusted}.ca"))
    tls_policy.enforce_peer_key_policy(context)
    accepted = case in ("rsa2048", "ec224", "ec256")
    with tls_server(folder, offered, version) as (port, observed):
        if accepted:
            tls_request(kind, context, port)
        else:
            with pytest.raises((ssl.SSLError, httpx.ConnectError)):
                tls_request(kind, context, port)
    assert bool(observed["application"]) is accepted
    if accepted:
        assert b"Bearer synthetic-darwin-test" in observed["application"]
    if accepted or case == "rsa2047":
        # The anchor was not sent by the server; inspect the actually verified chain.
        assert certificates[root] in examined


@pytest.mark.parametrize(
    "name, expected",
    [
        ("rsa1024", False),
        ("rsa2047", False),
        ("rsa2048", True),
        ("rsa4096", True),
        ("ec192", False),
        ("ec224", True),
        ("ec256", True),
        ("ec384", True),
    ],
)
def test_native_key_boundaries(certificates, name, expected):
    assert policy.certificate_key_meets_minimum(certificates[name]) is expected


@pytest.mark.parametrize("name, accepted", [("rsa2047", False), ("rsa2048", True)])
def test_native_server_identity(native_pki, monkeypatch, name, accepted):
    from inverter_dashboard import server_tls, tls_policy

    folder, _, certificates = native_pki
    certificate = folder / f"server-{name}.pem"
    certificate.write_text(ssl.DER_cert_to_PEM_cert(certificates[name]))
    config = SimpleNamespace(ssl_certfile=str(certificate), ssl_keyfile=str(folder / f"{name}.key"))
    # Retain production dispatch on Intel; use its native backend explicitly on ARM.
    if sys.platform == "darwin" and platform.machine() != "x86_64":
        monkeypatch.setattr(server_tls, "_certificate_key_ok", policy.certificate_key_meets_minimum)
    else:
        assert server_tls._certificate_key_ok is tls_policy._certificate_key_ok

    def load_identity():
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(config.ssl_certfile, config.ssl_keyfile)
        return context

    if accepted:
        assert isinstance(server_tls.server_context(config, load_identity), ssl.SSLContext)
    else:
        with pytest.raises(ssl.SSLError, match="below the security minimum"):
            server_tls.server_context(config, load_identity)


def test_native_malformed_der(certificates):
    for value in (b"not a certificate", certificates["rsa2048"][:24]):
        with pytest.raises(ValueError, match="valid DER"):
            policy.certificate_key_meets_minimum(value)


@pytest.mark.parametrize("value", [b"", "not bytes", None])
def test_invalid_input(value):
    with pytest.raises(ValueError, match="nonempty DER bytes"):
        policy.certificate_key_meets_minimum(value)


def test_import_does_not_load_frameworks(monkeypatch):
    load = Mock(side_effect=AssertionError("Framework loaded during import"))
    monkeypatch.setattr(ctypes, "CDLL", load)
    monkeypatch.setattr(sys, "platform", "linux")
    specification = importlib.util.spec_from_file_location("darwin_import_probe", policy.__file__)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    with pytest.raises(OSError, match="requires macOS"):
        module.certificate_key_meets_minimum(b"synthetic")
    load.assert_not_called()


@pytest.mark.parametrize(
    "failure, released",
    [
        ("data", []),
        ("certificate", [1]),
        ("key", [2, 1]),
        ("attributes", [3, 2, 1]),
        ("policy", [4, 3, 2, 1]),
        (None, [4, 3, 2, 1]),
    ],
)
def test_native_ownership_released_on_every_exit(monkeypatch, failure, released):
    release = Mock()
    native = object.__new__(policy._Native)
    native.cf = SimpleNamespace(
        CFDataCreate=lambda *args: None if failure == "data" else 1, CFRelease=release
    )
    native.security = SimpleNamespace(
        SecCertificateCreateWithData=lambda *args: None if failure == "certificate" else 2,
        SecCertificateCopyKey=lambda *args: None if failure == "key" else 3,
        SecKeyCopyAttributes=lambda *args: None if failure == "attributes" else 4,
    )
    native.meets_minimum = Mock(return_value=True)
    if failure == "policy":
        native.meets_minimum.side_effect = OSError("unavailable metadata")
    monkeypatch.setattr(policy, "_native", lambda: native)
    if failure:
        with pytest.raises((ValueError, OSError)):
            policy.certificate_key_meets_minimum(b"synthetic DER")
    else:
        assert policy.certificate_key_meets_minimum(b"synthetic DER")
    assert [call.args[0] for call in release.call_args_list] == released


@pytest.mark.parametrize(
    "mode, expected",
    [
        ("unknown", False),
        ("rsa", True),
        ("ec", True),
        ("missing", None),
        ("dictionary", None),
        ("type", None),
        ("number", None),
        ("conversion", None),
        ("negative", None),
    ],
)
def test_native_metadata_checked_before_use(mode, expected):
    native = object.__new__(policy._Native)
    native.key_type, native.key_bits, native.rsa, native.ec = 10, 11, 12, 13
    type_id = {1: 100, 2: 200, 3: 300}
    if mode in ("dictionary", "type", "number"):
        type_id[{"dictionary": 1, "type": 2, "number": 3}[mode]] = 999

    def number_value(reference, kind, output):
        assert reference == 3 and kind == 4
        ctypes.cast(output, ctypes.POINTER(ctypes.c_int64))[0] = -1 if mode == "negative" else 2048
        return mode != "conversion"

    native.cf = SimpleNamespace(
        CFGetTypeID=lambda ref: type_id[ref],
        CFDictionaryGetTypeID=lambda: 100,
        CFStringGetTypeID=lambda: 200,
        CFNumberGetTypeID=lambda: 300,
        CFDictionaryGetValue=lambda ref, key: (
            None if mode == "missing" else (2 if key == 10 else 3)
        ),
        CFNumberGetValue=number_value,
        CFEqual=lambda value, constant: constant == {"rsa": 12, "ec": 13}.get(mode),
    )
    if expected is None:
        with pytest.raises(OSError):
            native.meets_minimum(1)
    else:
        assert native.meets_minimum(1) is expected
