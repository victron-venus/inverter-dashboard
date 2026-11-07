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
    start_application,
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
        self.request("/fixture/seed", body={})
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
            result = start_application(
                "python",
                "mqtt",
                Path("/isolated-config"),
                OWNED + "-mqtt",
                OWNED,
                OWNED + ":candidate",
                credential,
            )
        self.assertIsNone(result)
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
