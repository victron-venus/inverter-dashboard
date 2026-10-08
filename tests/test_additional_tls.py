"""Actual MQTT and Web Push handshakes against disposable local certificate chains."""

import asyncio
import socket
import ssl
import threading
from contextlib import contextmanager

import pytest

from inverter_dashboard import push_transport, server
from inverter_dashboard.push_store import PushStore
from tests.test_push_transport import receiver
from tests.test_tls_policy import chains as chains  # noqa: PLC0414 - expose shared pytest fixture
from tests.test_tls_policy import (
    clean_environment as clean_environment,  # noqa: PLC0414 - expose fixture
)
from tests.test_tls_policy import peer


@contextmanager
def mqtt_peer(chain, version):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = context.maximum_version = version
    # A disposable peer accepts weak test certificates so the client is tested.
    context.set_ciphers("DEFAULT:@SECLEVEL=0")
    context.load_cert_chain(chain[0], chain[1])
    observed = {"application_bytes": b""}
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
                        packet = connection.recv(4096)
                        observed["application_bytes"] = packet
                        if not packet:
                            return
                        assert packet[0] == 0x10, "Expected a synthetic MQTT CONNECT"
                        connection.sendall(b"\x20\x02\x00\x00")
                        while connection.recv(4096):
                            pass
            except (ssl.SSLError, ConnectionError) as error:
                # Rejected peers and normal MQTT disconnects can close this socket.
                # Keep the diagnostic; each test also checks the received bytes.
                observed["connection_error"] = repr(error)
            except Exception as error:
                observed["unexpected"] = repr(error)

        thread = threading.Thread(target=receive, daemon=True)
        thread.start()
        try:
            yield listener.getsockname()[1], observed
        finally:
            thread.join(6)
            assert not thread.is_alive(), "Synthetic MQTT peer did not stop"
            assert "unexpected" not in observed, observed


@pytest.mark.parametrize("version", [ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3])
@pytest.mark.parametrize(
    "case", ["strong", "weak-2047-root", "weak-root", "untrusted", "wrong-host"]
)
async def test_mqtt_rejects_weak_verified_keys_before_connect(chains, monkeypatch, version, case):
    chain = chains.get(case, chains["strong"])
    trusted = chains["strong-ec"] if case == "untrusted" else chain
    host = "127.0.0.1" if case == "wrong-host" else "localhost"
    with mqtt_peer(chain, version) as (port, observed):
        for name, value in {
            "MQTT_HOST": host,
            "MQTT_PORT": port,
            "MQTT_TLS": True,
            "MQTT_CA_CERT": str(trusted[2]),
            "MQTT_USERNAME": "synthetic-user",
            "MQTT_PASSWORD": "synthetic-mqtt-credential",  # nosec B105 - disposable loopback fixture
        }.items():
            monkeypatch.setattr(server.config, name, value)
        error = None
        try:
            async with asyncio.timeout(5), server._make_mqtt_client():
                pass
        except server.MqttError as caught:
            error = caught
    accepted = case == "strong"
    assert bool(observed["application_bytes"]) is accepted
    assert (error is None) is accepted
    if accepted:
        assert b"synthetic-mqtt-credential" in observed["application_bytes"]


@pytest.mark.parametrize("version", [ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3])
@pytest.mark.parametrize(
    "case", ["strong", "weak-2047-root", "weak-root", "untrusted", "wrong-host"]
)
async def test_push_rejects_weak_verified_keys_before_post(
    chains, monkeypatch, tmp_path, version, case
):
    chain = chains.get(case, chains["strong"])
    trusted = chains["strong-ec"] if case == "untrusted" else chain
    monkeypatch.setenv("SSL_CERT_FILE", str(trusted[2]))
    # Only route the test provider to the disposable loopback listener. The actual
    # encryption, HTTP/TLS transport, certificate trust and hostname checks run.
    monkeypatch.setattr(
        push_transport, "EXACT_PUSH_HOSTS", frozenset({"localhost", "fcm.googleapis.com"})
    )
    _, _, subscription = receiver()
    host = "fcm.googleapis.com" if case == "wrong-host" else "localhost"
    subscription["endpoint"] = f"https://{host}/synthetic-push"
    transport = push_transport.PushTransport(PushStore(tmp_path), "mailto:test@example.com")
    with peer(chain, version) as (port, observed):

        async def resolve(self, host, port=0, family=socket.AF_INET):
            return [
                {
                    "hostname": host,
                    "host": "127.0.0.1",
                    "port": local_port,
                    "family": socket.AF_INET,
                    "proto": socket.IPPROTO_TCP,
                    "flags": socket.AI_NUMERICHOST,
                }
            ]

        local_port = port
        monkeypatch.setattr(push_transport.PublicPushResolver, "resolve", resolve)
        error = None
        try:
            status = await transport.send(
                subscription, {"sourceTimestampMs": 1700000000000, "body": "synthetic"}, 1700000001
            )
        except push_transport.aiohttp.ClientError as caught:
            error = caught
    accepted = case == "strong"
    assert bool(observed["application_bytes"]) is accepted
    assert (error is None) is accepted
    if accepted:
        assert status == 200
        assert b"Authorization: vapid" in observed["application_bytes"]
