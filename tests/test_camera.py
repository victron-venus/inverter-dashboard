"""Tests for camera event handling (handle_camera_event + routing)."""

import asyncio
import json

from inverter_dashboard import server


async def test_camera_event_json_payload():
    ms = server.MqttState()
    ms.handle_camera_event(
        json.dumps(
            {
                "agent_name": "Front Door",
                "video_url": "http://f/clip.mp4",
                "timestamp": "2026-08-24",
            }
        ).encode()
    )
    assert ms.camera_event == {
        "camera": "Front Door",
        "url": "http://f/clip.mp4",
        "ts": "2026-08-24",
    }


async def test_camera_event_raw_url_fallback():
    ms = server.MqttState()
    ms.handle_camera_event(b"http://frigate/api/snapshot.jpg")
    assert ms.camera_event == {
        "camera": "Camera",
        "url": "http://frigate/api/snapshot.jpg",
        "ts": "",
    }


async def test_camera_event_bad_json_is_raw():
    ms = server.MqttState()
    ms.handle_camera_event(b"{not json")
    assert ms.camera_event is not None
    assert "not json" in ms.camera_event["url"]


async def test_camera_topic_routing(monkeypatch):
    """on_message routes matching topics to handle_camera_event when configured."""
    monkeypatch.setattr(server.config, "CAMERA_TOPIC", "frigate/+/events")
    ms = server.MqttState()
    await ms.on_message("frigate/front/events", json.dumps({"agent_name": "front"}).encode())
    await asyncio.sleep(0)
    assert ms.camera_event["camera"] == "front"

    # non-matching topic ignored
    await ms.on_message("other/topic", b"http://x/y.mjpeg")
    assert ms.camera_event["camera"] == "front"
