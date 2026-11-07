#!/usr/bin/env python3
"""Synthetic MQTT/IGW/HA peers, confined to a Docker internal test network.

This is deliberately not a forwarding proxy. There is no outbound networking,
production configuration, external credentials, or configurable destination.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socketserver
import ssl
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CREDENTIAL_ENV = "CI_PACKAGED_BROWSER_TOKEN"


def credential_from_env(environment: dict | None = None) -> str:
    """Require a fresh bounded URL-safe credential supplied by the owning runner."""
    source = os.environ if environment is None else environment
    value = source.get(CREDENTIAL_ENV, "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", value, re.ASCII):
        raise ValueError("Missing or invalid ephemeral fixture credential")
    return value


PORTAL = "browserqa"
LOCK = threading.RLock()
CLIENTS: set = set()
MESSAGES: dict[str, bytes] = {}
CAPTURE: list[dict] = []
SNAPSHOT: dict = {}
EVENT_EPOCH = 0
HA_STATE = "off"
HA_READS = 0


def packet(header: int, payload: bytes) -> bytes:
    """Encode bounded MQTT 3.1.1 packets for the local peer."""
    length, encoded = len(payload), bytearray()
    while True:
        byte, length = length % 128, length // 128
        encoded.append(byte | (128 if length else 0))
        if not length:
            break
    return bytes([header]) + bytes(encoded) + payload


def mqtt_string(value: str) -> bytes:
    data = value.encode()
    return len(data).to_bytes(2, "big") + data


def topic_matches(pattern: str, topic: str) -> bool:
    """Support only standard MQTT +/# subscription matching."""
    parts, names = pattern.split("/"), topic.split("/")
    for i, part in enumerate(parts):
        if part == "#":
            return i == len(parts) - 1
        if i >= len(names) or part not in {"+", names[i]}:
            return False
    return len(parts) == len(names)


def publish(topic: str, value) -> None:
    data = json.dumps(value, separators=(",", ":")).encode()
    with LOCK:
        MESSAGES[topic] = data
        for client in CLIENTS:
            if any(topic_matches(p, topic) for p in client.subscriptions):
                client.send(packet(0x30, mqtt_string(topic) + data))


def seed(authority: bool = True, event: bool = True) -> None:
    """Publish source-aged warnings and a synthetic controller observation."""
    global SNAPSHOT, EVENT_EPOCH, HA_READS, HA_STATE
    EVENT_EPOCH = int(time.time()) - 4500
    inverter = {
        "version": "packaged-fixture",
        "dry_run": False,
        "booleans": {"charge_battery": False, "only_charging": False},
    }
    groups = {
        "system": {
            "0/Ac/Grid/L1/Power": 420,
            "0/Ac/Consumption/L1/Power": 850,
            "0/Dc/Pv/Power": 1200,
            "0/Dc/Battery/Soc": 73,
            "0/Dc/Battery/Voltage": 51.2,
            "0/Dc/Battery/Power": -300,
        },
        "battery": {"1/Soc": 73, "1/Dc/0/Voltage": 51.2, "1/CustomName": "Fixture battery"},
        "platform": {},
    }
    if event:
        groups["platform"] = {
            f"0/Notifications/1/{key}": value
            for key, value in {
                "DateTime": EVENT_EPOCH,
                "Type": 1,
                "DeviceName": "Fixture battery",
                "Active": False,
                "Acknowledged": False,
                "Description": "Fixture old alarm",
            }.items()
        }
    with LOCK:
        HA_READS = 0
        HA_STATE = "off"
        SNAPSHOT = {
            **groups,
            "inverter": inverter if authority else None,
            "capabilities": {"setpoint_override": True, "ess_mode": True},
        }
        MESSAGES.clear()
        for service, leaves in groups.items():
            for key, value in leaves.items():
                publish(f"N/{PORTAL}/{service}/{key}", {"value": value})
        if authority:
            publish("inverter/state", inverter)


class Broker(socketserver.BaseRequestHandler):
    """Minimal real MQTT peer: framing, subscription, retained delivery, captures."""

    def setup(self):
        self.subscriptions: list[str] = []
        self.output_lock = threading.Lock()
        self.request.settimeout(30)
        with LOCK:
            CLIENTS.add(self)

    def send(self, value: bytes):
        try:
            with self.output_lock:
                self.request.sendall(value)
        except OSError:
            self.request.close()

    def read_exact(self, size: int) -> bytes:
        value = bytearray()
        while len(value) < size:
            chunk = self.request.recv(size - len(value))
            if not chunk:
                raise EOFError
            value.extend(chunk)
        return bytes(value)

    def read_packet(self):
        header = self.read_exact(1)[0]
        size, multiplier = 0, 1
        for _ in range(4):
            value = self.read_exact(1)[0]
            size += (value & 127) * multiplier
            if size > 1024 * 1024:
                raise ValueError("MQTT fixture packet too large")
            if not value & 128:
                return header, self.read_exact(size)
            multiplier *= 128
        raise ValueError("Invalid MQTT fixture remaining length")

    def subscribe(self, data: bytes):
        position, patterns = 2, []
        while position < len(data):
            length = int.from_bytes(data[position : position + 2], "big")
            position += 2
            patterns.append(data[position : position + length].decode())
            position += length + 1
        with LOCK:
            self.subscriptions.extend(patterns)
            self.send(packet(0x90, data[:2] + bytes(len(patterns))))
            for topic, payload in MESSAGES.items():
                if any(topic_matches(p, topic) for p in patterns):
                    self.send(packet(0x31, mqtt_string(topic) + payload))

    def receive_publish(self, header: int, data: bytes):
        length = int.from_bytes(data[:2], "big")
        topic = data[2 : 2 + length].decode()
        position = 2 + length
        if (header >> 1) & 3:
            self.send(packet(0x40, data[position : position + 2]))
            position += 2
        if topic.startswith(("inverter/cmd/", "W/")):
            with LOCK:
                CAPTURE.append(
                    {"transport": "mqtt", "path": topic, "body": json.loads(data[position:])}
                )

    def handle(self):
        try:
            while True:
                header, data = self.read_packet()
                kind = header >> 4
                if kind == 1:
                    self.send(b"\x20\x02\x00\x00")
                elif kind == 8:
                    self.subscribe(data)
                elif kind == 3:
                    self.receive_publish(header, data)
                elif kind == 12:
                    self.send(b"\xd0\x00")
                elif kind == 14:
                    break
        except (OSError, EOFError, ValueError):
            return

    def finish(self):
        with LOCK:
            CLIENTS.discard(self)


def entity() -> dict:
    return {
        "entity_id": "switch.fixture_light",
        "state": HA_STATE,
        "attributes": {"friendly_name": "Fixture light"},
    }


class Handler(BaseHTTPRequestHandler):
    """Local control endpoints and authenticated synthetic HA/IGW APIs."""

    def log_message(self, *_args):
        return  # Synthetic bearer tokens do not belong in request logs either.

    def reply(self, status: int, value):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        global HA_READS
        if self.path == "/fixture/status":
            with LOCK:
                self.reply(
                    200,
                    {
                        "capture": CAPTURE,
                        "eventEpoch": EVENT_EPOCH,
                        "mqttClients": len(CLIENTS),
                        "haReads": HA_READS,
                    },
                )
            return
        if self.headers.get("Authorization") != f"Bearer {self.server.fixture_credential}":
            self.reply(401, {"error": "fixture authorization required"})
            return
        with LOCK:
            if self.path == "/v1/snapshot":
                self.reply(200, SNAPSHOT)
            elif self.path == "/api/states":
                HA_READS += 1
                self.reply(200, [entity()])
            elif self.path == "/api/states/switch.fixture_light":
                HA_READS += 1
                self.reply(200, entity())
            else:
                self.reply(404, {"error": "unknown fixture route"})

    def authenticated_ha_service_response(self, body):
        """Handle one HA attempt while the caller holds LOCK."""
        global HA_STATE
        # An invalid actuator request is still an attempted publication.
        # Record it before rejecting so the journal cannot hide it.
        CAPTURE.append({"transport": "ha", "path": self.path, "body": body})
        if (
            self.path != "/api/services/switch/toggle"
            or body.get("entity_id") != "switch.fixture_light"
        ):
            return 404, {}
        HA_STATE = "on" if HA_STATE == "off" else "off"
        return 200, {"ok": True}

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 <= length <= 65536:
            self.reply(413, {})
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self.reply(400, {})
            return
        response = (200, {"ok": True})
        with LOCK:
            if self.path == "/fixture/seed":
                CAPTURE.clear()
                seed()
            elif self.path == "/fixture/reconnect":
                MESSAGES.clear()
                for client in CLIENTS:
                    with suppress(OSError):
                        client.request.shutdown(2)
                    client.request.close()
                seed(authority=False, event=False)
            elif self.path == "/fixture/fresh-slot":
                publish(
                    f"N/{PORTAL}/platform/0/Notifications/1/Description",
                    {"value": "Fixture fresh alarm"},
                )
            elif self.headers.get("Authorization") != f"Bearer {self.server.fixture_credential}":
                self.reply(401, {})
                return
            elif self.path.startswith("/v1/commands/"):
                CAPTURE.append({"transport": "igw", "path": self.path, "body": body})
            elif self.path.startswith("/api/services/"):
                response = self.authenticated_ha_service_response(body)
            else:
                self.reply(404, {})
                return
        self.reply(*response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    args = parser.parse_args()
    credential = credential_from_env()
    seed()
    broker = socketserver.ThreadingTCPServer(("0.0.0.0", 1883), Broker)  # nosec B104
    broker.daemon_threads = True
    http = ThreadingHTTPServer(("0.0.0.0", 8090), Handler)  # nosec B104
    https = ThreadingHTTPServer(("0.0.0.0", 8443), Handler)  # nosec B104
    http.fixture_credential = https.fixture_credential = credential
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.cert, args.key)
    https.socket = context.wrap_socket(https.socket, server_side=True)
    for server in (broker, http):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    https.serve_forever()


if __name__ == "__main__":
    main()
