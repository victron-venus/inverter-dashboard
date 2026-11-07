#!/usr/bin/env python3
"""Capture partial branded Safari/Chrome smoke evidence on an isolated CI fixture.

Not an installed-PWA, software-keyboard, physical-device or OS notification test.
"""

import argparse
import base64
import hashlib
import json
import os
import platform

# Subprocess is limited to the literal read-only /usr/bin/sw_vers call below.
import subprocess  # nosec B404
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from native_acceptance import candidate_binding, read_json, require
from qualification_paths import workspace_path

UTC = timezone.utc


def utcnow():
    return datetime.now(UTC).isoformat()


def macos_host_identity():
    require(platform.system() == "Darwin", "macOS smoke must run on an actual macOS host")
    version = platform.mac_ver()[0]
    # No shell or caller-controlled executable/arguments.
    measured = subprocess.run(  # nosec B603
        ["/usr/bin/sw_vers", "-buildVersion"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    build = measured.stdout.strip()
    architecture = platform.machine()
    require(version and build and architecture, "Measured macOS host identity is incomplete")
    return {"osName": "macOS", "osVersion": version, "osBuild": build, "architecture": architecture}


def loopback_url(value, scheme):
    parsed = urllib.parse.urlsplit(value)
    require(
        parsed.scheme == scheme and parsed.hostname in ("localhost", "127.0.0.1", "::1"),
        "Only isolated loopback fixtures/drivers are allowed",
    )
    require(
        not parsed.username
        and not parsed.password
        and parsed.path in ("", "/")
        and not parsed.query
        and not parsed.fragment,
        "Origin must not contain credentials/path/query",
    )
    return value.rstrip("/")


MAX_WEBDRIVER_RESPONSE = 8 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, _request, _file, _code, _message, _headers, _url):
        return None


def decode_driver_response(response):
    length = response.headers.get("Content-Length")
    if length is not None:
        require(
            length.isdecimal() and int(length) <= MAX_WEBDRIVER_RESPONSE,
            "WebDriver response exceeds limit",
        )
    require(
        response.headers.get_content_type() == "application/json", "WebDriver response is not JSON"
    )
    data = response.read(MAX_WEBDRIVER_RESPONSE + 1)
    require(len(data) <= MAX_WEBDRIVER_RESPONSE, "WebDriver response exceeds limit")
    document = json.loads(data)
    require(
        isinstance(document, dict) and set(document) == {"value"},
        "Invalid WebDriver response schema",
    )
    result = document["value"]
    if isinstance(result, dict) and result.get("error"):
        raise ValueError("WebDriver command failed: " + str(result["error"])[:200])
    return result


class WebDriver:
    def __init__(self, origin):
        self.origin = loopback_url(origin, "http")
        self.session = None
        # The driver must never inherit proxy routing or follow a redirect away
        # from the checked loopback origin, including redirects to file/FTP URLs.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def call(self, method, route, payload=None):
        require(method in {"GET", "POST", "DELETE"}, "Unsupported WebDriver method")
        require(
            isinstance(route, str)
            and route.startswith("/")
            and not route.startswith("//")
            and all(ord(char) >= 32 for char in route)
            and "#" not in route,
            "Invalid WebDriver route",
        )
        encoded = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            self.origin + route, encoded, {"Content-Type": "application/json"}, method=method
        )
        try:
            with self.opener.open(request, timeout=45) as response:
                return decode_driver_response(response)
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            raise ValueError(f"WebDriver rejected HTTP status {status}") from None

    def command(self, method, route, payload=None):
        return self.call(method, "/session/" + self.session + route, payload)

    def script(self, source, arguments=None):
        return self.command("POST", "/execute/sync", {"script": source, "args": arguments or []})

    def until(self, source):
        for _ in range(60):
            result = self.script(source)
            if result:
                return result
            time.sleep(0.5)
        raise ValueError("Native UI condition not observed before timeout")


def execute(candidate, base_url, driver_url, platform_name, runtime, output):
    require(
        os.environ.get("GITHUB_ACTIONS") == "true",
        "Native WebDriver execution is hosted-only; local UI must use cua_repl",
    )
    base_url = loopback_url(base_url, "https")
    driver = WebDriver(loopback_url(driver_url, "http"))
    require(
        (platform_name, runtime)
        in (
            ("macOS", "native-host"),
            ("macOS", "native-device"),
            ("iOS", "native-device"),
            ("iOS", "native-simulator"),
            ("Android", "native-device"),
            ("Android", "native-emulator"),
        ),
        "Unsupported platform/runtime",
    )
    browser_name = "chrome" if platform_name == "Android" else "safari"
    capabilities = {"browserName": browser_name, "acceptInsecureCerts": False}
    if platform_name == "macOS":
        capabilities["platformName"] = "mac"
    elif platform_name == "iOS":
        capabilities.update(
            {"platformName": "ios", "safari:useSimulator": runtime == "native-simulator"}
        )
    else:
        capabilities["goog:chromeOptions"] = {"androidPackage": "com.android.chrome"}
    output.mkdir(parents=True, exist_ok=True)
    evidence_path = workspace_path("native-smoke.json", root=output, kind="file")
    screenshot_path = workspace_path("native-smoke.png", root=output, kind="file")
    evidence_path.unlink(missing_ok=True)
    record = {
        "schemaVersion": 1,
        "scope": "native-smoke",
        "status": "failed",
        "candidate": candidate_binding(candidate),
        "startedAt": utcnow(),
        "requestedPlatform": platform_name,
        "runtime": runtime,
        "scenarios": [],
        "nativeComplete": False,
        "osNotificationObserved": False,
        "limitations": [
            "No native consent or installed-PWA test",
            "No OS notification observation",
            "No software keyboard observation",
            "Synthetic backend; no actuator writes",
        ],
    }
    try:
        if platform_name == "macOS":
            record["hostIdentity"] = macos_host_identity()
        created = driver.call("POST", "/session", {"capabilities": {"alwaysMatch": capabilities}})
        driver.session = created["sessionId"]
        actual = created["capabilities"]
        require(
            actual.get("browserName", "").lower() == browser_name and actual.get("browserVersion"),
            "Wrong/missing branded browser identity",
        )
        actual_platform = str(actual.get("platformName", "")).lower()
        expected_platforms = {"macOS": ("mac", "macos"), "iOS": ("ios",), "Android": ("android",)}
        require(
            actual_platform in expected_platforms[platform_name], "Driver selected the wrong OS"
        )
        record["driverCapabilities"] = {
            key: value
            for key, value in actual.items()
            if key
            in (
                "browserName",
                "browserVersion",
                "platformName",
                "safari:platformVersion",
                "safari:platformBuildVersion",
                "safari:useSimulator",
                "safari:deviceType",
            )
        }
        driver.command("POST", "/timeouts", {"implicit": 0, "pageLoad": 45000, "script": 15000})
        driver.command("POST", "/url", {"url": base_url + "/"})
        identity = driver.script(
            'return {userAgent:navigator.userAgent,platform:navigator.platform,secureContext:isSecureContext,standalone:matchMedia("(display-mode: standalone)").matches,serviceWorker:"serviceWorker" in navigator}'
        )
        require(
            identity["secureContext"] and identity["serviceWorker"],
            "Secure context/service workers unavailable",
        )
        record["runtimeIdentity"] = identity
        record["scenarios"].append({"id": "branded-browser-secure-runtime", "status": "passed"})
        driver.until(
            'return document.body.textContent.includes("73%") && !!document.querySelector("[data-testid=transport-status][data-connected=true]")'
        )
        record["scenarios"].append({"id": "candidate-telemetry-hydrates", "status": "passed"})
        element = driver.command(
            "POST", "/element", {"using": "css selector", "value": 'button[aria-label="Settings"]'}
        )
        element_id = element["element-6066-11e4-a52e-4f735466cecf"]
        driver.command("POST", "/element/" + element_id + "/click", {})
        driver.until('return !!document.querySelector("dialog[open]")')
        require(
            driver.script(
                """return !!document.querySelector('section[aria-label="System notifications"]')"""
            ),
            "Notification settings missing",
        )
        record["scenarios"].append(
            {"id": "settings-and-notification-section-open", "status": "passed"}
        )
        # Secure-context fetch observes bytes actually returned to native Safari.
        worker_hash = driver.command(
            "POST",
            "/execute/async",
            {
                "script": 'const done=arguments[arguments.length-1];fetch("/notifications-sw.js",{cache:"no-store"}).then(r=>r.arrayBuffer()).then(b=>crypto.subtle.digest("SHA-256",b)).then(b=>done(Array.from(new Uint8Array(b)).map(v=>v.toString(16).padStart(2,"0")).join(""))).catch(()=>done(null));',
                "args": [],
            },
        )
        require(
            worker_hash == candidate["frontendFiles"]["notifications-sw.js"],
            "Native-served worker is not the candidate",
        )
        record["workerSha256"] = worker_hash
        record["scenarios"].append(
            {"id": "native-served-worker-matches-candidate", "status": "passed"}
        )
        screenshot = base64.b64decode(driver.command("GET", "/screenshot"), validate=True)
        require(screenshot.startswith(b"\x89PNG\r\n\x1a\n"), "Invalid native screenshot")
        screenshot_path.write_bytes(screenshot)
        record["screenshot"] = {
            "path": "native-smoke.png",
            "sha256": hashlib.sha256(screenshot).hexdigest(),
        }
        record["status"] = "passed"
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        record["error"] = str(exc)[:1000]
        raise
    finally:
        if driver.session:
            try:
                driver.command("DELETE", "")
            except (ValueError, OSError):
                record["sessionCleanupFailed"] = True
        record["finishedAt"] = utcnow()
        evidence_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--driver-url", default="http://127.0.0.1:4444")
    parser.add_argument("--platform", choices=["macOS", "iOS", "Android"], required=True)
    parser.add_argument(
        "--runtime",
        choices=["native-host", "native-device", "native-simulator", "native-emulator"],
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    candidate = workspace_path(args.candidate, kind="file", must_exist=True)
    output = workspace_path(args.output, kind="directory")
    execute(
        read_json(candidate),
        args.base_url,
        args.driver_url,
        args.platform,
        args.runtime,
        output,
    )


if __name__ == "__main__":
    main()
