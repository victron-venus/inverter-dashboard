#!/usr/bin/env python3
"""Exercise the network fixtures on loopback without Docker or real equipment."""

import json
import os
import socket
import socketserver
import subprocess  # nosec B404
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import packaged_browser_fixture as fixture
from packaged_browser_acceptance import app_environment, cleanup_owned, fetch, verify_local_docker


class FixtureTests(unittest.TestCase):
    def setUp(self):
        with fixture.LOCK:
            fixture.CAPTURE.clear()
            fixture.CLIENTS.clear()
            fixture.seed()
        self.broker = socketserver.ThreadingTCPServer(("127.0.0.1", 0), fixture.Broker)
        self.broker.daemon_threads = True
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), fixture.Handler)
        for server in (self.broker, self.http):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.http.server_address[1]}"

    def tearDown(self):
        for server in (self.broker, self.http):
            server.shutdown()
            server.server_close()

    def request(self, route, auth=True, body=None):
        headers = {"Authorization": f"Bearer {fixture.TOKEN}"} if auth else {}
        request = urllib.request.Request(
            self.base + route,
            headers=headers,
            data=json.dumps(body).encode() if body is not None else None,
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=2) as response:
            return json.load(response)

    def test_source_aged_event_and_gateway_authorization(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/v1/snapshot", auth=False)
        self.assertEqual(caught.exception.code, 401)
        caught.exception.close()
        snapshot = self.request("/v1/snapshot")
        self.assertEqual(snapshot["platform"]["0/Notifications/1/Description"], "Fixture old alarm")
        status = self.request("/fixture/status", auth=False)
        self.assertEqual(snapshot["platform"]["0/Notifications/1/DateTime"], status["eventEpoch"])

    def test_ha_read_and_command_are_synthetic(self):
        self.assertEqual(
            self.request("/api/states/switch.fixture_light")["entity_id"], "switch.fixture_light"
        )
        self.request("/api/services/switch/toggle", body={"entity_id": "switch.fixture_light"})
        captured = self.request("/fixture/status")["capture"]
        self.assertEqual(
            captured,
            [
                {
                    "transport": "ha",
                    "path": "/api/services/switch/toggle",
                    "body": {"entity_id": "switch.fixture_light"},
                }
            ],
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/api/services/switch/toggle", body={"entity_id": "switch.real"})
        caught.exception.close()

    def test_mqtt_real_handshake_and_subscribe(self):
        with socket.create_connection(self.broker.server_address, timeout=2) as client:
            client.sendall(
                fixture.packet(
                    0x10,
                    fixture.mqtt_string("MQTT")
                    + b"\x04\x02\x00\x3c"
                    + fixture.mqtt_string("unit-fixture"),
                )
            )
            self.assertEqual(client.recv(4), b"\x20\x02\x00\x00")
            client.sendall(
                fixture.packet(0x82, b"\x00\x01" + fixture.mqtt_string("inverter/state") + b"\x00")
            )
            self.assertEqual(client.recv(5), b"\x90\x03\x00\x01\x00")
            self.assertIn(b"inverter/state", client.recv(4096))
            client.sendall(b"\xe0\x00")

    def test_unknown_routes_do_not_forward(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/https://real-equipment.invalid")
        self.assertEqual(caught.exception.code, 404)
        caught.exception.close()

    def test_environments_never_include_operational_destinations(self):
        for mode in ("mqtt", "igw"):
            env = app_environment(mode)
            self.assertEqual(env["MQTT_HOST"], "fixtures" if mode == "mqtt" else "")
            self.assertEqual(env["GATEWAY_URL"], "" if mode == "mqtt" else "https://fixtures:8443")
            self.assertEqual(env["SELF_UPDATE_ENABLED"], "false")
            self.assertEqual(env["SSL_CERT_FILE"], "/qa-config/fixture.crt")
            self.assertFalse(env["HTTP_PROXY"] or env["HTTPS_PROXY"] or env["ALL_PROXY"])

    def test_topic_filters_do_not_cross_prefixes(self):
        self.assertTrue(fixture.topic_matches("N/+/system/#", "N/browserqa/system/0/Dc/Pv/Power"))
        self.assertFalse(fixture.topic_matches("W/#", "N/browserqa/system/0/Dc/Pv/Power"))
        self.assertFalse(fixture.topic_matches("N/+", "N/browserqa/system"))


class RunnerSafetyTests(unittest.TestCase):
    def test_remote_docker_environment_is_refused_before_cli(self):
        for variable, value in (
            ("DOCKER_HOST", "tcp://production.invalid:2375"),
            ("DOCKER_CONTEXT", "production"),
        ):
            with (
                patch.dict(os.environ, {variable: value}, clear=True),
                patch("packaged_browser_acceptance.call") as cli,
            ):
                with self.assertRaises(ValueError):
                    verify_local_docker()
                cli.assert_not_called()

    def test_remote_selected_context_is_refused(self):
        remote = json.dumps([{"Endpoints": {"docker": {"Host": "ssh://production.invalid"}}}])
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("packaged_browser_acceptance.call", return_value=remote),
            self.assertRaises(ValueError),
        ):
            verify_local_docker()

    def test_http_capture_cannot_follow_external_or_file_inputs(self):
        with patch("urllib.request.build_opener") as opener:
            for origin in (
                "https://equipment.invalid",
                "file:///etc/passwd",
                "http://127.0.0.1:80/other",
            ):
                with self.assertRaises(ValueError):
                    fetch(origin, "/")
            opener.assert_not_called()

    def test_unwritable_diagnostics_cannot_skip_any_cleanup(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("packaged_browser_acceptance.call", return_value="") as cli,
            patch.object(Path, "write_text", side_effect=OSError("disk full")),
        ):
            cleanup_owned(["fixture-owned"], "network-owned", "image-owned", Path(temporary))
        arguments = [item.args for item in cli.call_args_list]
        self.assertIn(("docker", "rm", "-f", "fixture-owned"), arguments)
        self.assertIn(("docker", "network", "rm", "network-owned"), arguments)
        self.assertIn(("docker", "image", "rm", "image-owned"), arguments)

    def test_log_and_network_failure_do_not_skip_owned_cleanup(self):
        calls = []

        def command(*args, **_kwargs):
            calls.append(args)
            if args[1] in {"logs", "network"}:
                raise subprocess.TimeoutExpired(args, 30)
            return ""

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("packaged_browser_acceptance.call", side_effect=command),
            self.assertRaisesRegex(RuntimeError, "cleanup incomplete"),
        ):
            cleanup_owned(["fixture-owned"], "network-owned", "image-owned", Path(temporary))
        self.assertIn(("docker", "rm", "-f", "fixture-owned"), calls)
        self.assertIn(("docker", "image", "rm", "image-owned"), calls)


if __name__ == "__main__":
    unittest.main()
