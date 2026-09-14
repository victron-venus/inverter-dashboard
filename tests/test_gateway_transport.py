"""IGW credentials stay on the configured HTTPS origin with verified TLS."""

import asyncio
import contextlib
import ssl

import httpx
import pytest
import trustme
from pydantic import ValidationError

from inverter_dashboard import config, gateway


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setattr(config, "GATEWAY_URL", "https://gateway.example.com:9151")
    monkeypatch.setattr(config, "GATEWAY_API_TOKEN", "test-bearer")
    monkeypatch.setattr(config, "GATEWAY_ACCESS_CLIENT_ID", "test-access-id")
    monkeypatch.setattr(config, "GATEWAY_ACCESS_CLIENT_SECRET", "test-access-secret")


INVALID_URLS = [
    "",
    "http://gateway.example.com:9150",
    "http://127.0.0.1:9150",
    "http://inverter-gateway.gateway.svc.cluster.local:8080",
    "//gateway.example.com",
    "https:///",
    "https://user:secret@gateway.example.com",
    "https://@gateway.example.com",
    "https://gateway.example.com/base",
    "https://gateway.example.com/.",
    "https://gateway.example.com/..",
    "https://gateway.example.com?token=secret",
    "https://gateway.example.com#fragment",
    "https://gateway.example.com\\@other.example",
    "https://gateway.example.com\n",
    " https://gateway.example.com",
    "https://gateway.example.com:0",
    "https://gateway.example.com:65536",
]


@pytest.mark.parametrize("value", INVALID_URLS[1:])
def test_settings_reject_unsafe_gateway_url_without_echoing_credentials(value):
    with pytest.raises(ValidationError) as error:
        config.Config(_env_file=None, GATEWAY_URL=value)
    assert "secret" not in str(error.value)


def test_native_https_configuration_accepts_bearer_without_access_credentials(monkeypatch):
    settings = config.Config(
        _env_file=None,
        GATEWAY_ENABLED=True,
        GATEWAY_URL="https://gateway.example.com:9151/",
        GATEWAY_API_TOKEN="test-bearer",
        GATEWAY_ACCESS_CLIENT_ID="",
        GATEWAY_ACCESS_CLIENT_SECRET="",
    )
    assert settings.GATEWAY_URL == "https://gateway.example.com:9151"
    for name in (
        "GATEWAY_URL",
        "GATEWAY_API_TOKEN",
        "GATEWAY_ACCESS_CLIENT_ID",
        "GATEWAY_ACCESS_CLIENT_SECRET",
    ):
        monkeypatch.setattr(config, name, getattr(settings, name))
    assert gateway.build_headers() == {
        "User-Agent": "inverter-dashboard/gateway",
        "Authorization": "Bearer test-bearer",
    }


@pytest.mark.parametrize("client_id,secret", [("private-id", ""), ("", "private-secret")])
def test_partial_cloudflare_pair_fails_configuration_without_echoing_credentials(client_id, secret):
    with pytest.raises(ValidationError) as error:
        config.Config(
            _env_file=None, GATEWAY_ACCESS_CLIENT_ID=client_id, GATEWAY_ACCESS_CLIENT_SECRET=secret
        )
    assert "must be set together" in str(error.value)
    assert "private-id" not in str(error.value)
    assert "private-secret" not in str(error.value)


def test_enabled_gateway_requires_url():
    with pytest.raises(ValidationError, match="requires an HTTPS GATEWAY_URL"):
        config.Config(_env_file=None, GATEWAY_ENABLED=True, GATEWAY_URL="")


@pytest.mark.parametrize("value", INVALID_URLS)
@pytest.mark.parametrize("operation", ["snapshot", "command"])
async def test_invalid_url_fails_before_headers_or_transport(
    monkeypatch, credentials, value, operation
):
    monkeypatch.setattr(config, "GATEWAY_URL", value)

    def forbidden(*_args, **_kwargs):
        pytest.fail("unsafe URL reached credential construction or network transport")

    monkeypatch.setattr(gateway, "build_headers", forbidden)
    monkeypatch.setattr(gateway, "_new_gateway_client", forbidden)
    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        with pytest.raises(ValueError, match="HTTPS origin"):
            if operation == "snapshot":
                await gateway.fetch_snapshot(client)
            else:
                await gateway.post_command("silence_alarm")


@pytest.mark.parametrize("operation", ["snapshot", "command"])
async def test_partial_access_pair_fails_before_network(monkeypatch, credentials, operation):
    monkeypatch.setattr(config, "GATEWAY_ACCESS_CLIENT_SECRET", "")

    def forbidden(*_args, **_kwargs):
        pytest.fail("partial Access credentials reached network transport")

    monkeypatch.setattr(gateway, "_new_gateway_client", forbidden)
    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        with pytest.raises(ValueError, match="must be set together"):
            if operation == "snapshot":
                await gateway.fetch_snapshot(client)
            else:
                await gateway.post_command("silence_alarm")


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize(
    "location",
    [
        "https://other.example/receive",
        "http://gateway.example.com:9151/receive",
        "/same-origin-redirect",
        "//other.example/receive",
    ],
)
@pytest.mark.parametrize("operation", ["snapshot", "command"])
async def test_redirect_cannot_replay_credentials(
    monkeypatch, credentials, status, location, operation
):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(status, headers={"Location": location})
        return httpx.Response(200, json={})

    # A caller changing the client default must not weaken the per-request policy.
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    monkeypatch.setattr(gateway, "_new_gateway_client", lambda: client)
    with pytest.raises(httpx.HTTPStatusError) as error:
        if operation == "snapshot":
            async with client:
                await gateway.fetch_snapshot(client)
        else:
            await gateway.post_command("silence_alarm", {"test": True})
    assert error.value.response.status_code == status
    assert len(requests) == 1
    request = requests[0]
    assert request.url.host == "gateway.example.com"
    assert request.url.scheme == "https"
    assert request.headers["Authorization"] == "Bearer test-bearer"
    assert request.headers["CF-Access-Client-Id"] == "test-access-id"
    assert request.headers["CF-Access-Client-Secret"] == "test-access-secret"
    assert request.method == ("GET" if operation == "snapshot" else "POST")


@pytest.mark.parametrize("trust", ["trusted", "unknown_ca", "wrong_hostname"])
@pytest.mark.parametrize("operation", ["snapshot", "command"])
async def test_gateway_tls_verifies_ca_and_hostname(
    monkeypatch, credentials, tmp_path, trust, operation
):
    ca = trustme.CA()
    certificate = ca.issue_cert("localhost")
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    certificate.configure_cert(server_context)
    ca_file = tmp_path / "ca.pem"
    (trustme.CA() if trust == "unknown_ca" else ca).cert_pem.write_to_path(ca_file)
    monkeypatch.setenv("SSL_CERT_FILE", str(ca_file))
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setattr(config, "GATEWAY_ACCESS_CLIENT_ID", "")
    monkeypatch.setattr(config, "GATEWAY_ACCESS_CLIENT_SECRET", "")
    requests = []

    async def serve(reader, writer):
        try:
            request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=3)
            requests.append(request)
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Content-Length: 2\r\nConnection: close\r\n\r\n{}"
            )
            await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError, ssl.SSLError):
                await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0, ssl=server_context)
    host = "127.0.0.1" if trust == "wrong_hostname" else "localhost"
    monkeypatch.setattr(
        config, "GATEWAY_URL", f"https://{host}:{server.sockets[0].getsockname()[1]}"
    )

    async def request_gateway():
        if operation == "snapshot":
            async with gateway._new_gateway_client() as client:
                return await gateway.fetch_snapshot(client)
        return await gateway.post_command("silence_alarm", {})

    async with server:
        if trust == "trusted":
            await asyncio.wait_for(request_gateway(), timeout=5)
            assert len(requests) == 1
            assert b"Authorization: Bearer test-bearer\r\n" in requests[0]
            assert b"CF-Access-" not in requests[0]
        else:
            with pytest.raises(httpx.ConnectError, match="CERTIFICATE_VERIFY_FAILED"):
                await asyncio.wait_for(request_gateway(), timeout=5)
            assert requests == []
