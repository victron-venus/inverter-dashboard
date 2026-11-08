#!/usr/bin/env python3
"""Exercise the network fixtures on loopback without Docker or real equipment."""

import json
import os
import secrets
import socket
import socketserver
import subprocess  # nosec B404
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import packaged_browser_fixture as fixture
from packaged_browser_acceptance import (
    MAX_LOG_CHARS,
    app_environment,
    call,
    cleanup_owned,
    execute,
    fetch,
    fixture_runtime_user,
    start_application,
    start_fixture,
    verify_local_docker,
    wait_ready,
)

OWNED = "browser-qa-0123456789abcdef"


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.credential = secrets.token_urlsafe(32)
        with fixture.LOCK:
            fixture.CAPTURE.clear()
            fixture.CLIENTS.clear()
            fixture.seed()
        self.broker = socketserver.ThreadingTCPServer(("127.0.0.1", 0), fixture.Broker)
        self.broker.daemon_threads = True
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), fixture.Handler)
        self.http.fixture_credential = self.credential
        for server in (self.broker, self.http):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.http.server_address[1]}"

    def tearDown(self):
        for server in (self.broker, self.http):
            server.shutdown()
            server.server_close()

    def request(self, route, auth=True, body=None):
        headers = {"Authorization": f"Bearer {self.credential}"} if auth else {}
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

    def test_seed_requires_fresh_ha_polling_and_clears_prior_switch_state(self):
        self.request("/api/states")
        self.request("/api/services/switch/toggle", body={"entity_id": "switch.fixture_light"})
        self.assertGreater(self.request("/fixture/status")["haReads"], 0)
        self.request("/fixture/seed", body={"transport": "igw"})
        reset = self.request("/fixture/status")
        self.assertEqual(reset["haReads"], 0)
        self.assertEqual(reset["capture"], [])
        self.assertEqual(self.request("/api/states/switch.fixture_light")["state"], "off")
        self.assertEqual(self.request("/fixture/status")["haReads"], 1)

    def test_invalid_ha_actuator_attempts_are_recorded_without_changing_state(self):
        attempts = [
            ("/api/services/switch/toggle", {"entity_id": "switch.unconfigured"}),
            ("/api/services/switch/turn_on", {"entity_id": "switch.fixture_light"}),
        ]
        for route, body in attempts:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request(route, body=body)
            self.assertEqual(caught.exception.code, 404)
            caught.exception.close()
        self.assertEqual(
            self.request("/fixture/status")["capture"],
            [{"transport": "ha", "path": route, "body": body} for route, body in attempts],
        )
        self.assertEqual(self.request("/api/states/switch.fixture_light")["state"], "off")

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

    def mqtt_connect(self):
        client = socket.create_connection(self.broker.server_address, timeout=2)
        self.addCleanup(client.close)
        client.sendall(
            fixture.packet(
                0x10,
                fixture.mqtt_string("MQTT")
                + b"\x04\x02\x00\x3c"
                + fixture.mqtt_string("readiness-control"),
            )
        )
        self.assertEqual(client.recv(4), b"\x20\x02\x00\x00")
        return client

    def mqtt_packet(self, client):
        def read_exact(size):
            data = bytearray()
            while len(data) < size:
                chunk = client.recv(size - len(data))
                if not chunk:
                    raise EOFError("Fixture socket closed before packet completed")
                data.extend(chunk)
            return bytes(data)

        header = read_exact(1)[0]
        size, multiplier = 0, 1
        for _ in range(4):
            value = read_exact(1)[0]
            size += (value & 127) * multiplier
            if not value & 128:
                return header, read_exact(size)
            multiplier *= 128
        raise ValueError("Unbounded fixture packet")

    def mqtt_subscribe_all(self, client):
        with fixture.LOCK:
            retained_count = len(fixture.MESSAGES)
        client.sendall(fixture.packet(0x82, b"\x00\x01" + fixture.mqtt_string("#") + b"\x00"))
        self.assertEqual(self.mqtt_packet(client), (0x90, b"\x00\x01\x00"))
        for _ in range(retained_count):
            self.assertEqual(self.mqtt_packet(client)[0], 0x31)
        return self.request("/fixture/status")["mqtt"]["readySession"]

    def assert_control_rejected(self, route, body, code=409):
        before = self.request("/fixture/status")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(route, body=body)
        self.assertEqual(caught.exception.code, code)
        caught.exception.close()
        after = self.request("/fixture/status")
        self.assertEqual(after["capture"], before["capture"])
        self.assertEqual(after["eventEpoch"], before["eventEpoch"])

    def test_seed_waits_for_real_subscription_then_publishes_live_once(self):
        client = self.mqtt_connect()
        self.request("/api/services/switch/toggle", body={"entity_id": "switch.fixture_light"})
        status = self.request("/fixture/status")["mqtt"]
        self.assertEqual(status["activeSessions"], 1)
        self.assertIsNone(status["readySession"])
        client.sendall(
            fixture.packet(0x82, b"\x00\x02" + fixture.mqtt_string("inverter/state") + b"\x00")
        )
        self.assertEqual(self.mqtt_packet(client), (0x90, b"\x00\x02\x00"))
        self.assertEqual(self.mqtt_packet(client)[0], 0x31)
        self.assertIsNone(self.request("/fixture/status")["mqtt"]["readySession"])
        self.assert_control_rejected(
            "/fixture/seed",
            {"transport": "mqtt", "generation": status["generation"], "sessionId": 1},
        )
        binding = self.mqtt_subscribe_all(client)
        self.assertIsNotNone(binding)
        accepted = self.request("/fixture/seed", body={"transport": "mqtt", **binding})
        expected_publications = len(fixture.required_topics())
        self.assertEqual(accepted["livePublications"], expected_publications)
        topics = set()
        for _ in range(expected_publications):
            header, payload = self.mqtt_packet(client)
            self.assertEqual(header, 0x30, "Accepted live seed must not be retained replay")
            length = int.from_bytes(payload[:2], "big")
            topics.add(payload[2 : 2 + length].decode())
        self.assertEqual(topics, fixture.required_topics())
        self.assertEqual(self.request("/fixture/status")["capture"], [])

    def test_reconnect_rejects_old_identity_and_waits_for_new_subscriptions(self):
        old = self.mqtt_connect()
        binding = self.mqtt_subscribe_all(old)
        with fixture.LOCK:
            retired = next(iter(fixture.CLIENTS))
        self.request("/api/services/switch/toggle", body={"entity_id": "switch.fixture_light"})
        generation = self.request("/fixture/reconnect", body={"transport": "mqtt", **binding})
        self.assertGreater(generation["generation"], binding["generation"])
        self.assertIsNone(self.request("/fixture/status")["mqtt"]["readySession"])
        for action in ("seed", "fresh-slot", "reconnect"):
            self.assert_control_rejected("/fixture/" + action, {"transport": "mqtt", **binding})
        current = self.mqtt_connect()
        self.assertIsNone(self.request("/fixture/status")["mqtt"]["readySession"])
        new_binding = self.mqtt_subscribe_all(current)
        self.assertEqual(new_binding["generation"], generation["generation"])
        self.assertNotEqual(new_binding["sessionId"], binding["sessionId"])
        # A delayed old finish cannot retire the new connection or satisfy readiness.
        with fixture.LOCK:
            fixture.CLIENTS.add(retired)
        self.assertEqual(self.request("/fixture/status")["mqtt"]["readySession"], new_binding)
        retired.finish()
        accepted = self.request("/fixture/fresh-slot", body={"transport": "mqtt", **new_binding})
        self.assertEqual(accepted["livePublications"], 1)
        header, payload = self.mqtt_packet(current)
        self.assertEqual(header, 0x30)
        self.assertIn(b"Fixture fresh alarm", payload)
        self.assertEqual(len(self.request("/fixture/status")["capture"]), 1)

    def test_failed_live_send_cannot_acknowledge_seed(self):
        client = self.mqtt_connect()
        binding = self.mqtt_subscribe_all(client)
        with fixture.LOCK:
            handler = next(iter(fixture.CLIENTS))
        with (
            patch.object(handler, "send", return_value=False),
            self.assertRaises(urllib.error.HTTPError) as caught,
        ):
            self.request("/fixture/seed", body={"transport": "mqtt", **binding})
        self.assertEqual(caught.exception.code, 503)
        caught.exception.close()

    def test_socket_send_error_retires_session_without_successful_acknowledgement(self):
        class ClosedSocket:
            closed = False

            def sendall(self, _data):
                raise OSError("synthetic closed socket")

            def close(self):
                self.closed = True

        handler = fixture.Broker.__new__(fixture.Broker)
        handler.output_lock = threading.Lock()
        handler.request = ClosedSocket()
        handler.retired = False
        self.assertFalse(handler.send(b"synthetic packet"))
        self.assertTrue(handler.retired)
        self.assertTrue(handler.request.closed)
        self.assertFalse(handler.output_lock.locked())

    def test_ambiguous_subscribers_cannot_clear_command_journal(self):
        one = self.mqtt_connect()
        binding = self.mqtt_subscribe_all(one)
        two = self.mqtt_connect()
        self.mqtt_subscribe_all(two)
        self.request("/api/services/switch/toggle", body={"entity_id": "switch.fixture_light"})
        self.assertIsNone(self.request("/fixture/status")["mqtt"]["readySession"])
        self.assert_control_rejected("/fixture/seed", {"transport": "mqtt", **binding})

    def test_unknown_routes_do_not_forward(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/https://real-equipment.invalid")
        self.assertEqual(caught.exception.code, 404)
        caught.exception.close()

    def test_environments_never_include_operational_destinations(self):
        for mode in ("mqtt", "igw"):
            env = app_environment(mode, self.credential)
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
    def test_fixture_runs_as_owner_without_widening_key_or_docker_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            key = directory / "fixture.key"
            key.write_bytes(b"synthetic-key-permission-control")
            key.chmod(0o600)
            credential = secrets.token_urlsafe(32)
            with (
                patch("packaged_browser_acceptance.sys.platform", "linux"),
                patch("packaged_browser_acceptance.call", return_value="") as cli,
            ):
                start_fixture(directory, directory, OWNED + "-fixtures", OWNED, credential)
            command = cli.call_args.args
            self.assertEqual(command[command.index("--user") + 1], f"{os.getuid()}:{os.getgid()}")
            self.assertIn("--cap-drop=ALL", command)
            self.assertIn("--read-only", command)
            self.assertIn("--security-opt=no-new-privileges", command)
            self.assertIn(f"{directory}:/qa-config:ro", command)
            self.assertNotIn("--cap-add", command)
            self.assertNotIn(credential, " ".join(command))
            self.assertEqual(key.stat().st_mode & 0o777, 0o600)

    def test_fixture_rejects_root_foreign_owner_and_nonprivate_or_linked_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            key = directory / "fixture.key"
            key.write_bytes(b"synthetic-key-permission-control")
            for mode in (0o644, 0o640):
                key.chmod(mode)
                with (
                    patch("packaged_browser_acceptance.sys.platform", "linux"),
                    self.assertRaises(ValueError),
                ):
                    fixture_runtime_user(directory)
            key.chmod(0o600)
            for uid, gid in ((0, os.getgid()), (os.getuid(), 0), (os.getuid() + 1, os.getgid())):
                with (
                    patch("packaged_browser_acceptance.sys.platform", "linux"),
                    patch("packaged_browser_acceptance.os.getuid", return_value=uid),
                    patch("packaged_browser_acceptance.os.getgid", return_value=gid),
                    self.assertRaises(ValueError),
                ):
                    fixture_runtime_user(directory)
            target = directory / "actual.key"
            key.rename(target)
            key.symlink_to(target)
            with (
                patch("packaged_browser_acceptance.sys.platform", "linux"),
                self.assertRaises(ValueError),
            ):
                fixture_runtime_user(directory)

    def test_runner_rejects_outside_or_linked_outputs_before_docker(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("packaged_browser_acceptance.call") as cli,
        ):
            root = Path(temporary)
            inputs = root / "inputs"
            inputs.mkdir()
            (root / "link").symlink_to(root.parent, target_is_directory=True)
            for output in (root.parent / "outside-package", root / "link" / "output"):
                with self.assertRaises(ValueError):
                    execute(root, inputs, output, 1)
            cli.assert_not_called()

    def test_cleanup_cannot_target_unowned_resources(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("packaged_browser_acceptance.call") as cli,
        ):
            root = Path(temporary)
            output = root / "output"
            output.mkdir()
            for names, network, image, destination in (
                (["other-container"], OWNED, OWNED + ":candidate", output),
                ([OWNED + "-fixtures"], "production", OWNED + ":candidate", output),
                ([OWNED + "-fixtures"], OWNED, "production:latest", output),
            ):
                with self.assertRaises(ValueError):
                    cleanup_owned(names, network, image, destination, root=root)
            cli.assert_not_called()

    def test_credentials_are_required_bounded_and_never_embedded(self):
        values = {secrets.token_urlsafe(32) for _ in range(3)}
        self.assertEqual(len(values), 3)
        for credential in values:
            self.assertEqual(
                fixture.credential_from_env({fixture.CREDENTIAL_ENV: credential}), credential
            )
        for credential in ("", "short", "x" * 129, "\n" + "x" * 43, "x" * 42 + "/"):
            with self.assertRaises(ValueError):
                fixture.credential_from_env({fixture.CREDENTIAL_ENV: credential})

    def test_readiness_errors_remain_bounded_and_report_safe_class(self):
        credential = secrets.token_urlsafe(32)
        with (
            patch("packaged_browser_acceptance.time.monotonic", side_effect=[0, 0, 61]),
            patch("packaged_browser_acceptance.time.sleep") as sleep,
            patch("packaged_browser_acceptance.fetch", side_effect=OSError("private URL detail")),
            self.assertRaisesRegex(TimeoutError, "synthetic source: OSError") as caught,
        ):
            wait_ready("http://127.0.0.1:12345", "mqtt", credential)
        self.assertNotIn("private URL", str(caught.exception))
        sleep.assert_called_once_with(0.25)

    def test_generated_credential_is_environment_only_in_docker_arguments(self):
        credential = secrets.token_urlsafe(32)
        with patch("packaged_browser_acceptance.call", return_value="") as cli:
            start_application(
                "python",
                "mqtt",
                Path("/isolated-config"),
                OWNED + "-mqtt",
                OWNED,
                OWNED + ":candidate",
                credential,
            )
        cli.assert_called_once()
        first = cli.call_args_list[0]
        self.assertNotIn("-p", first.args)
        self.assertIn("dashboard.packaged-qa=" + OWNED, first.args)
        self.assertNotIn(credential, " ".join(first.args))
        self.assertEqual(first.kwargs["environment"]["DASHBOARD_SECRET"], credential)
        self.assertIn("DASHBOARD_SECRET", first.args)

    def test_missing_diagnostics_directory_does_not_prevent_cleanup(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("packaged_browser_acceptance.call", return_value="") as cli,
        ):
            root = Path(temporary)
            output = root / "gone"
            with self.assertRaisesRegex(RuntimeError, "diagnostics unavailable"):
                cleanup_owned(
                    [OWNED + "-fixtures"],
                    OWNED,
                    OWNED + ":candidate",
                    output,
                    root=root,
                )
        self.assertIn(
            ("docker", "rm", "-f", OWNED + "-fixtures"), [item.args for item in cli.call_args_list]
        )

    def test_container_diagnostics_preserve_both_streams_on_failure_and_bound_text(self):
        output = call(
            sys.executable,
            "-c",
            "import sys; print('stdout diagnostic'); print('stderr diagnostic', file=sys.stderr); sys.exit(7)",
            check=False,
            combine_logs=True,
        )
        self.assertIn("stdout diagnostic", output)
        self.assertIn("stderr diagnostic", output)
        completed = subprocess.CompletedProcess(
            [], 7, "x" * (MAX_LOG_CHARS + 100) + "tail diagnostic"
        )
        with patch("packaged_browser_acceptance.subprocess.run", return_value=completed) as process:
            output = call("docker", "logs", "--tail", "1000", OWNED, check=False, combine_logs=True)
        self.assertEqual(process.call_args.kwargs["stderr"], subprocess.STDOUT)
        self.assertLess(len(output), MAX_LOG_CHARS + 100)
        self.assertTrue(output.endswith("tail diagnostic"))
        self.assertIn("truncated", output)

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
        credential = secrets.token_urlsafe(32)
        with patch("urllib.request.build_opener") as opener:
            for origin in (
                "https://equipment.invalid",
                "file:///etc/passwd",
                "http://127.0.0.1:80/other",
            ):
                with self.assertRaises(ValueError):
                    fetch(origin, "/", credential=credential)
            opener.assert_not_called()

    def test_unwritable_diagnostics_cannot_skip_any_cleanup(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("packaged_browser_acceptance.call", return_value="") as cli,
            patch.object(Path, "open", side_effect=OSError("disk full")),
        ):
            root = Path(temporary)
            output = root / "output"
            output.mkdir()
            with self.assertRaisesRegex(RuntimeError, "diagnostics unavailable"):
                cleanup_owned(
                    [OWNED + "-fixtures"],
                    OWNED,
                    OWNED + ":candidate",
                    output,
                    root=root,
                )
        arguments = [item.args for item in cli.call_args_list]
        self.assertIn(("docker", "rm", "-f", OWNED + "-fixtures"), arguments)
        self.assertIn(("docker", "network", "rm", OWNED), arguments)
        self.assertIn(("docker", "image", "rm", OWNED + ":candidate"), arguments)

    def test_invalid_utf8_diagnostics_cannot_skip_owned_cleanup(self):
        calls = []

        def command(*args, **_kwargs):
            calls.append(args)
            if args[1] == "logs":
                # Produce the actual decoder exception raised by subprocess text mode.
                return b"\xff".decode("utf-8")
            return ""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output"
            output.mkdir()
            with (
                patch("packaged_browser_acceptance.call", side_effect=command),
                self.assertRaisesRegex(RuntimeError, "diagnostics unavailable"),
            ):
                cleanup_owned([OWNED + "-fixtures"], OWNED, OWNED + ":candidate", output, root=root)
            self.assertIn("UnicodeDecodeError", (output / "cleanup-errors.log").read_text())
        self.assertIn(("docker", "rm", "-f", OWNED + "-fixtures"), calls)
        self.assertIn(("docker", "network", "rm", OWNED), calls)
        self.assertIn(("docker", "image", "rm", OWNED + ":candidate"), calls)

    def test_rejected_diagnostics_path_still_removes_only_owned_resources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside"
            outside.mkdir()
            workspace = root / "workspace"
            workspace.mkdir()
            linked = workspace / "linked"
            linked.symlink_to(outside, target_is_directory=True)
            expected = [
                ("docker", "rm", "-f", OWNED + "-fixtures"),
                ("docker", "network", "rm", OWNED),
                ("docker", "image", "rm", OWNED + ":candidate"),
            ]
            for output in (outside, linked):
                with (
                    patch("packaged_browser_acceptance.call", return_value="") as cli,
                    self.assertRaisesRegex(RuntimeError, "diagnostics unavailable"),
                ):
                    cleanup_owned(
                        [OWNED + "-fixtures"],
                        OWNED,
                        OWNED + ":candidate",
                        output,
                        root=workspace,
                    )
                self.assertEqual([item.args for item in cli.call_args_list], expected)
                self.assertEqual(list(outside.iterdir()), [])

    def test_diagnostic_file_symlinks_cannot_redirect_log_or_failure_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            protected = root / "protected.txt"
            protected.write_text("untouched")
            output = root / "output"
            output.mkdir()
            (output / f"{OWNED}-fixtures.log").symlink_to(protected)
            (output / "cleanup-errors.log").symlink_to(protected)
            with (
                patch("packaged_browser_acceptance.call", return_value="container log") as cli,
                self.assertRaisesRegex(RuntimeError, "diagnostics unavailable"),
            ):
                cleanup_owned([OWNED + "-fixtures"], OWNED, OWNED + ":candidate", output, root=root)
            self.assertEqual(protected.read_text(), "untouched")
            self.assertIn(
                ("docker", "image", "rm", OWNED + ":candidate"),
                [item.args for item in cli.call_args_list],
            )

    def test_log_and_network_failure_do_not_skip_owned_cleanup(self):
        calls = []

        def command(*args, **_kwargs):
            calls.append(args)
            if args[1] in {"logs", "network"}:
                raise subprocess.TimeoutExpired(args, 30)
            return ""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output"
            output.mkdir()
            with (
                patch("packaged_browser_acceptance.call", side_effect=command),
                self.assertRaisesRegex(RuntimeError, "cleanup incomplete"),
            ):
                cleanup_owned(
                    [OWNED + "-fixtures"],
                    OWNED,
                    OWNED + ":candidate",
                    output,
                    root=root,
                )
        self.assertIn(("docker", "rm", "-f", OWNED + "-fixtures"), calls)
        self.assertIn(("docker", "image", "rm", OWNED + ":candidate"), calls)

    def test_only_exact_owned_resource_absence_is_successful_cleanup(self):
        messages = {
            "rm": f"Error response from daemon: No such container: {OWNED}-fixtures",
            "network": f"Error response from daemon: network {OWNED} not found",
            "image": f"Error response from daemon: No such image: {OWNED}:candidate",
        }
        calls = []

        def command(*args, **_kwargs):
            calls.append(args)
            if args[1] in messages:
                raise subprocess.CalledProcessError(1, args, stderr=messages[args[1]] + "\n")
            return "fixture diagnostics"

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output"
            output.mkdir()
            with patch("packaged_browser_acceptance.call", side_effect=command):
                cleanup_owned([OWNED + "-fixtures"], OWNED, OWNED + ":candidate", output, root=root)
            self.assertFalse((output / "cleanup-errors.log").exists())
        self.assertEqual([args[1] for args in calls], ["logs", "rm", "network", "image"])

    def test_unrelated_not_found_and_wrong_resource_errors_remain_fatal(self):
        for detail in (
            "Error response from daemon: authorization plugin qa-auth not found",
            "Error response from daemon: network another-network not found",
        ):
            calls = []

            def command(*args, observed=calls, failure=detail, **_kwargs):
                observed.append(args)
                if args[1] == "network":
                    raise subprocess.CalledProcessError(1, args, stderr=failure)
                return ""

            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                output = root / "output"
                output.mkdir()
                with (
                    patch("packaged_browser_acceptance.call", side_effect=command),
                    self.assertRaisesRegex(RuntimeError, "cleanup incomplete"),
                ):
                    cleanup_owned(
                        [OWNED + "-fixtures"], OWNED, OWNED + ":candidate", output, root=root
                    )
                self.assertIn(detail, (output / "cleanup-errors.log").read_text())
            self.assertIn(("docker", "image", "rm", OWNED + ":candidate"), calls)


if __name__ == "__main__":
    unittest.main()
