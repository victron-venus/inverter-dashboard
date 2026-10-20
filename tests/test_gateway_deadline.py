"""A responsive trickle must not hold the selected IGW poll open indefinitely."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from inverter_dashboard import gateway


class SnapshotStream(httpx.AsyncByteStream):
    """Synthetic incremental response; no gateway, credentials, or device writes."""

    def __init__(self, delay, chunks):
        self.delay = delay
        self.chunks = chunks
        self.closed = False
        self.delivered = 0

    async def __aiter__(self):
        for _ in range(self.chunks):
            await asyncio.sleep(self.delay)
            self.delivered += 1
            yield b" "
        yield b'{"battery": {}}'

    async def aclose(self):
        self.closed = True


async def test_snapshot_total_deadline_closes_trickling_response(monkeypatch):
    monkeypatch.setattr(gateway.config, "GATEWAY_URL", "https://gateway.example")
    monkeypatch.setattr(gateway, "build_headers", dict)
    monkeypatch.setattr(gateway, "REQUEST_TIMEOUT_SECS", 0.05)
    stream = SnapshotStream(0.01, 100)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, stream=stream))
    ) as client:
        with pytest.raises(TimeoutError, match=r"IGW snapshot exceeded 0\.05s overall deadline"):
            await gateway.fetch_snapshot(client)
    assert 0 < stream.delivered < stream.chunks
    assert stream.closed


async def test_snapshot_completed_within_deadline_is_accepted(monkeypatch):
    monkeypatch.setattr(gateway.config, "GATEWAY_URL", "https://gateway.example")
    monkeypatch.setattr(gateway, "build_headers", dict)
    monkeypatch.setattr(gateway, "REQUEST_TIMEOUT_SECS", 1)
    stream = SnapshotStream(0, 3)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, stream=stream))
    ) as client:
        assert await gateway.fetch_snapshot(client) == {"battery": {}}
    assert stream.closed


async def test_poller_invalidates_timeout_then_accepts_next_snapshot(monkeypatch, caplog):
    monkeypatch.setattr(gateway.config, "GATEWAY_URL", "https://gateway.example")
    monkeypatch.setattr(gateway, "build_headers", dict)
    monkeypatch.setattr(gateway, "REQUEST_TIMEOUT_SECS", 0.05)
    monkeypatch.setattr(gateway.config, "GATEWAY_POLL_INTERVAL", 0.5)
    slow = SnapshotStream(0.01, 100)
    attempts = []
    applied = []
    statuses = []
    owner = SimpleNamespace(
        gateway_connected=True, mqtt_connected=False, gateway_polls=0, gateway_errors=0
    )

    def handler(_request):
        attempts.append(None)
        if len(attempts) == 1:
            return httpx.Response(200, stream=slow)
        return httpx.Response(200, json={"battery": {"0/Soc": 73}})

    monkeypatch.setattr(
        gateway,
        "_new_gateway_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    async def apply(snapshot):
        applied.append(snapshot)

    async def status():
        statuses.append(owner.gateway_connected)

    await asyncio.wait_for(
        gateway.gateway_poll_loop(owner, apply, status, is_current=lambda: not applied), 2
    )
    assert len(attempts) == 2
    assert applied == [{"battery": {"0/Soc": 73}}]
    assert statuses == [False]
    assert owner.gateway_errors == 1 and owner.gateway_connected
    assert slow.closed
    assert "IGW poll failed: IGW snapshot exceeded 0.05s overall deadline" in caplog.text


@pytest.mark.parametrize(
    "error", [TimeoutError("upstream timeout"), asyncio.CancelledError("cancelled")]
)
async def test_snapshot_preserves_unrelated_timeout_or_cancellation(monkeypatch, error):
    monkeypatch.setattr(gateway.config, "GATEWAY_URL", "https://gateway.example")
    monkeypatch.setattr(gateway, "build_headers", dict)
    monkeypatch.setattr(gateway, "REQUEST_TIMEOUT_SECS", 1)

    def handler(_request):
        raise error

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(type(error)) as caught:
            await gateway.fetch_snapshot(client)
    assert caught.value is error
