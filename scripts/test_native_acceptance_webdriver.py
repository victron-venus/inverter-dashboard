"""Protocol/capture unit tests; no browser, driver or device is started."""

import base64
import copy
import io
import json
import tempfile
import threading
import unittest
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import native_acceptance as gate
import native_acceptance_capture as capture
import native_acceptance_webdriver as driver


class NativeToolTests(unittest.TestCase):
    def test_loopback_only(self):
        self.assertEqual(
            driver.loopback_url("https://127.0.0.1:123/", "https"), "https://127.0.0.1:123"
        )
        for url in (
            "https://example.com",
            "https://127.0.0.1.evil.test",
            "http://127.0.0.1",
            "https://u:p@localhost",
            "https://localhost/path",
            "https://localhost?token=secret",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                driver.loopback_url(url, "https")

    def test_local_ui_execution_fails_before_session(self):
        with (
            patch.dict("os.environ", {"GITHUB_ACTIONS": ""}),
            self.assertRaisesRegex(ValueError, "hosted-only"),
        ):
            driver.execute(
                {},
                "https://127.0.0.1",
                "http://127.0.0.1:4444",
                "macOS",
                "native-host",
                Path("/unused"),
            )

    def test_macos_capture_records_measured_version_build_and_architecture(self):
        with (
            patch.object(driver.platform, "system", return_value="Darwin"),
            patch.object(driver.platform, "mac_ver", return_value=("15.7.1", ("", "", ""), "")),
            patch.object(driver.platform, "machine", return_value="arm64"),
            patch.object(driver.subprocess, "run", return_value=Mock(stdout="24G231\n")) as command,
        ):
            self.assertEqual(
                driver.macos_host_identity(),
                {
                    "osName": "macOS",
                    "osVersion": "15.7.1",
                    "osBuild": "24G231",
                    "architecture": "arm64",
                },
            )
            self.assertEqual(command.call_args.kwargs["timeout"], 10)

    def test_macos_capture_rejects_non_macos_host(self):
        with (
            patch.object(driver.platform, "system", return_value="Linux"),
            self.assertRaisesRegex(ValueError, "actual macOS"),
        ):
            driver.macos_host_identity()

    def protocol_server(self):
        paths = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                paths.append(self.path)
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/must-not-be-followed")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"value":{"ready":true}}')

            def log_message(self, *_args):
                # Keep fixture HTTP logs quiet; each request is asserted through paths above.
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(lambda: thread.join(timeout=2))
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}", paths

    def test_driver_does_not_follow_redirect(self):
        origin, paths = self.protocol_server()
        transport = driver.WebDriver(origin)
        with self.assertRaisesRegex(ValueError, "HTTP status 302"):
            transport.call("GET", "/redirect")
        self.assertEqual(paths, ["/redirect"])

    def test_driver_ignores_proxy_environment(self):
        origin, paths = self.protocol_server()
        with patch.dict(
            "os.environ",
            {
                "http_proxy": "http://127.0.0.1:1",
                "HTTP_PROXY": "http://127.0.0.1:1",
                "all_proxy": "http://127.0.0.1:1",
                "NO_PROXY": "",
                "no_proxy": "",
            },
        ):
            self.assertEqual(driver.WebDriver(origin).call("GET", "/status"), {"ready": True})
        self.assertEqual(paths, ["/status"])

    def response(self, body, content_type="application/json", length=None):
        result = io.BytesIO(body)
        result.headers = Message()
        result.headers["Content-Type"] = content_type
        if length is not None:
            result.headers["Content-Length"] = str(length)
        return result

    def test_driver_rejects_oversized_header_and_stream(self):
        large_header = self.response(b"", length=driver.MAX_WEBDRIVER_RESPONSE + 1)
        with self.assertRaisesRegex(ValueError, "exceeds limit"):
            driver.decode_driver_response(large_header)
        large_stream = self.response(b"x" * (driver.MAX_WEBDRIVER_RESPONSE + 1))
        with self.assertRaisesRegex(ValueError, "exceeds limit"):
            driver.decode_driver_response(large_stream)

    def test_driver_requires_json_response_schema(self):
        html_response = self.response(b"{}", content_type="text/html")
        with self.assertRaisesRegex(ValueError, "not JSON"):
            driver.decode_driver_response(html_response)
        for value in ({}, [], {"value": None, "unexpected": 1}):
            invalid_response = self.response(json.dumps(value).encode())
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "schema"):
                driver.decode_driver_response(invalid_response)
        self.assertIsNone(driver.decode_driver_response(self.response(b'{"value":null}')))

    def test_driver_validates_origin_at_construction(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            driver.WebDriver("http://example.com")

    def test_driver_preserves_bounded_network_timeout(self):
        transport = driver.WebDriver("http://127.0.0.1:4444")
        response = self.response(b'{"value":true}')
        with patch.object(transport.opener, "open", return_value=response) as opened:
            self.assertTrue(transport.call("GET", "/status"))
        self.assertEqual(opened.call_args.kwargs["timeout"], 45)

    def test_capture_is_pending_and_never_overwrites(self):
        candidate = {
            "repository": "victron-venus/test",
            "sourceSha": "a" * 40,
            "runId": 1,
            "runAttempt": 1,
            "candidateArtifactId": 2,
            "candidateSha256": "b" * 64,
        }
        policy = gate.read_json(
            Path(__file__).resolve().parents[1] / ".github/native-acceptance-policy.json"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "capture"
            capture.initialize(candidate, policy, root, "observer")
            review = gate.read_json(root / "native-review.pending.json")
            self.assertEqual(review["status"], "pending")
            with self.assertRaisesRegex(ValueError, "not accepted"):
                gate.verify(candidate, policy, review, root)
            with self.assertRaisesRegex(ValueError, "already exists"):
                capture.initialize(candidate, policy, root, "observer")

    def test_invalid_capture_inputs_leave_no_directory_or_partial_forms(self):
        candidate = {
            "repository": "victron-venus/test",
            "sourceSha": "a" * 40,
            "runId": 1,
            "runAttempt": 1,
            "candidateArtifactId": 2,
            "candidateSha256": "b" * 64,
        }
        policy = gate.read_json(
            Path(__file__).resolve().parents[1] / ".github/native-acceptance-policy.json"
        )
        malformed_policy = copy.deepcopy(policy)
        malformed_policy["targets"][-1]["id"] = "../escape"
        duplicate_policy = copy.deepcopy(policy)
        duplicate_policy["targets"].append(duplicate_policy["targets"][0])
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "capture"
            for bad_candidate, bad_policy in (
                ({}, policy),
                (candidate, {}),
                (candidate, malformed_policy),
                (candidate, duplicate_policy),
            ):
                with self.assertRaises(ValueError):
                    capture.initialize(bad_candidate, bad_policy, output, "observer")
                self.assertFalse(output.exists())

    def native_observation(self, output, delete_fails=False, secure=True):
        candidate = {
            "repository": "victron-venus/test",
            "sourceSha": "a" * 40,
            "runId": 1,
            "runAttempt": 1,
            "candidateArtifactId": 2,
            "candidateSha256": "b" * 64,
            "frontendFiles": {"notifications-sw.js": "c" * 64},
        }
        transport = Mock(session=None)
        transport.call.return_value = {
            "sessionId": "fixture",
            "capabilities": {
                "browserName": "safari",
                "browserVersion": "18.5",
                "platformName": "mac",
            },
        }
        transport.script.side_effect = [{"secureContext": secure, "serviceWorker": True}, True]

        def command(method, route, payload=None):
            if method == "DELETE" and delete_fails:
                raise ValueError("synthetic cleanup refusal")
            if route == "/execute/async":
                return True if "csp-status" in payload["script"] else "c" * 64
            if route == "/element":
                return {"element-6066-11e4-a52e-4f735466cecf": "element"}
            if route == "/screenshot":
                return base64.b64encode(b"\x89PNG\r\n\x1a\nsynthetic-unit-fixture").decode()
            return None

        transport.command.side_effect = command
        with (
            patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}),
            patch.object(driver, "WebDriver", return_value=transport),
            patch.object(driver, "macos_host_identity", return_value={"osName": "macOS"}),
        ):
            return driver.execute(
                candidate,
                "https://127.0.0.1:443",
                "http://127.0.0.1:4444",
                "macOS",
                "native-host",
                output,
            )

    def test_child_success_is_provisional_until_parent_finalization(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            result = self.native_observation(output)
            self.assertEqual(result["status"], "pending-parent-validation")
            self.assertTrue(result["sessionCleanupSucceeded"])
            self.assertTrue(result["cspProbeObserved"])
            self.assertFalse((output / "native-smoke.json").exists())
            self.assertEqual(
                gate.read_json(output / "native-smoke.provisional.json")["status"],
                "pending-parent-validation",
            )

    def test_delete_session_failure_cannot_produce_passing_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            with self.assertRaisesRegex(ValueError, "session cleanup failed"):
                self.native_observation(output, delete_fails=True)
            result = gate.read_json(output / "native-smoke.provisional.json")
            self.assertEqual(result["status"], "failed")
            self.assertTrue(result["sessionCleanupFailed"])
            self.assertFalse(result["sessionCleanupSucceeded"])
            self.assertFalse((output / "native-smoke.json").exists())

    def test_cleanup_failure_preserves_the_original_observation_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            with self.assertRaisesRegex(ValueError, "Secure context"):
                self.native_observation(output, delete_fails=True, secure=False)
            result = gate.read_json(output / "native-smoke.provisional.json")
            self.assertEqual(result["status"], "failed")
            self.assertIn("Secure context", result["error"])
            self.assertTrue(result["sessionCleanupFailed"])


if __name__ == "__main__":
    unittest.main()
