#!/usr/bin/env python3
"""Capture partial branded Safari/Chrome smoke evidence on an isolated CI fixture.

Not an installed-PWA, software-keyboard, physical-device or OS notification test.
"""

import argparse
import base64
import hashlib
import http.client
import json
import os
import platform
import re
import signal

# Subprocess is limited to the literal read-only /usr/bin/sw_vers call below.
import subprocess  # nosec B404
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from native_acceptance import candidate_binding, read_json, require
from qualification_paths import workspace_path


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


WEBDRIVER_CALL_TIMEOUT = 45
MAX_WEBDRIVER_RESPONSE = 8 * 1024 * 1024
MAX_WEBDRIVER_ERROR_RESPONSE = 16 * 1024
JSON_CONTENT_TYPE = "application/json"
W3C_ERROR_CODES = frozenset(
    {
        "invalid argument",
        "invalid session id",
        "no such driver",
        "session not created",
        "unknown error",
        "unknown command",
        "unsupported operation",
        "timeout",
        "script timeout",
        "javascript error",
        "unexpected alert open",
        "no such window",
    }
)


def diagnostic_message(value):
    """Bound a controlled hosted-driver message; omit common credential/path values."""
    value = " ".join("".join(char if char.isprintable() else " " for char in value).split())
    value = re.sub(r"(?i)\b(?:bearer|basic)\s+\S+", "[credential]", value)
    # An assignment may contain escaped quotes or an unterminated value. Do not
    # attempt to parse credentials: omit this controlled diagnostic message whole.
    if re.search(
        r"(?i)\b(?:authorization|(?:access|refresh|id)[_-]?token|token|password|"
        r"(?:client[_-]?)?secret|(?:api|client)[_-]?key|cookie|session[_-]?id)"
        r"[\"']?\s*[:=]",
        value,
    ):
        return "[credential-bearing message omitted]"
    value = re.sub(r"(?i)\b[a-z][a-z0-9+.-]*://\S+", "[url]", value)
    value = re.sub(r"(?:[A-Za-z]:[\\/]|/)[^\s\"']+", "[path]", value)
    value = re.sub(r"[A-Za-z0-9_+/=-]{24,}", "[opaque]", value)
    return value


class DiagnosticReadDeadline(TimeoutError):
    """The original WebDriver call budget has expired during diagnostics."""


class DiagnosticTimerUnavailable(RuntimeError):
    """This process context cannot safely own a temporary diagnostic timer."""


def diagnostic_timer_expired(_signum, _frame):
    raise DiagnosticReadDeadline("WebDriver diagnostic read deadline exceeded")


def remaining_diagnostic_time(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DiagnosticReadDeadline("WebDriver diagnostic read deadline exceeded")
    return remaining


@contextmanager
def diagnostic_read_deadline(deadline):
    """Bound blocking error reads in the standalone POSIX main thread only."""
    remaining_diagnostic_time(deadline)
    if (
        not hasattr(signal, "setitimer")
        or threading.current_thread() is not threading.main_thread()
    ):
        raise DiagnosticTimerUnavailable("Diagnostic timer context is unavailable")
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise DiagnosticTimerUnavailable("Another owner has an active alarm")
    previous_handler = signal.getsignal(signal.SIGALRM)
    if previous_handler is None:
        raise DiagnosticTimerUnavailable("Existing alarm handler cannot be restored")
    try:
        signal.signal(signal.SIGALRM, diagnostic_timer_expired)
        signal.setitimer(signal.ITIMER_REAL, remaining_diagnostic_time(deadline))
        yield
    finally:
        try:
            signal.setitimer(signal.ITIMER_REAL, 0)
        finally:
            signal.signal(signal.SIGALRM, previous_handler)


def driver_error_details(response, deadline):
    """Read a small W3C error only; arbitrary response bodies are never retained."""
    details = {"responseKind": "unavailable"}
    length = response.headers.get("Content-Length")
    if length is not None and (
        not length.isdecimal() or len(length) > 6 or int(length) > MAX_WEBDRIVER_ERROR_RESPONSE
    ):
        return {"responseKind": "invalid-or-oversized-length"}
    if response.headers.get_content_type() != JSON_CONTENT_TYPE:
        return {"responseKind": "non-json"}
    try:
        with diagnostic_read_deadline(deadline):
            body = response.read(MAX_WEBDRIVER_ERROR_RESPONSE + 1)
        if len(body) > MAX_WEBDRIVER_ERROR_RESPONSE:
            return {"responseKind": "oversized-body"}
        document = json.loads(body)
    except DiagnosticReadDeadline:
        return {"responseKind": "deadline-exceeded"}
    except DiagnosticTimerUnavailable:
        return {"responseKind": "deadline-unavailable"}
    except (OSError, ValueError, RecursionError, http.client.HTTPException):
        return details
    if not isinstance(document, dict) or set(document) != {"value"}:
        return {"responseKind": "invalid-schema"}
    value = document["value"]
    if not isinstance(value, dict) or not isinstance(value.get("error"), str):
        return {"responseKind": "invalid-schema"}
    details = {
        "responseKind": "w3c-error",
        "errorCode": value["error"] if value["error"] in W3C_ERROR_CODES else "unrecognized",
    }
    message = value.get("message")
    if isinstance(message, str):
        redacted = diagnostic_message(message)
        details["message"] = redacted[:768]
        details["messageTruncated"] = len(redacted) > 768
    return details


class WebDriverHTTPError(ValueError):
    def __init__(self, status, details):
        self.diagnostic = {"httpStatus": status, **details}
        super().__init__(f"WebDriver rejected HTTP status {status}: " + json.dumps(details))


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
        response.headers.get_content_type() == JSON_CONTENT_TYPE, "WebDriver response is not JSON"
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
        deadline = time.monotonic() + WEBDRIVER_CALL_TIMEOUT
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
            self.origin + route, encoded, {"Content-Type": JSON_CONTENT_TYPE}, method=method
        )
        try:
            with self.opener.open(request, timeout=WEBDRIVER_CALL_TIMEOUT) as response:
                return decode_driver_response(response)
        except urllib.error.HTTPError as error:
            status = error.code
            try:
                details = driver_error_details(error, deadline)
            finally:
                error.close()
            raise WebDriverHTTPError(status, details) from None

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


def finalize_observation(driver, record, evidence_path):
    """Persist only provisional success, after successful native session cleanup."""
    cleanup_failed = False
    if driver.session:
        try:
            driver.command("DELETE", "")
            record["sessionCleanupSucceeded"] = True
        except (ValueError, OSError) as error:
            cleanup_failed = True
            record["sessionCleanupFailed"] = True
            if isinstance(error, WebDriverHTTPError):
                record["cleanupDriverFailure"] = error.diagnostic
    observed_success = record["status"] == "pending-parent-validation"
    if cleanup_failed:
        record["status"] = "failed"
        record.setdefault("error", "Native WebDriver session cleanup failed")
    record["finishedAt"] = utcnow()
    with evidence_path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(record, indent=2) + "\n")
    if cleanup_failed and observed_success:
        raise ValueError("Native WebDriver session cleanup failed")


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
    evidence_path = workspace_path(
        "native-smoke.provisional.json", root=output, kind="file", new=True
    )
    screenshot_path = workspace_path("native-smoke.png", root=output, kind="file", new=True)
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
        "sessionCleanupSucceeded": False,
        "cspProbeObserved": False,
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
        csp_probe = driver.command(
            "POST",
            "/execute/async",
            {
                "script": """const done=arguments[arguments.length-1];
const origin='https://127.0.0.1:65534'; let ended=false;
const finish=value=>{if(ended)return;ended=true;clearTimeout(timer);removeEventListener('securitypolicyviolation',onViolation);done(value)};
const poll=()=>fetch('/__native/csp-status',{cache:'no-store'}).then(r=>r.json()).then(r=>{if(ended)return;if(r.probeReports===1)finish(true);else setTimeout(poll,100)}).catch(()=>finish(false));
const onViolation=event=>{let blocked;try{blocked=new URL(event.blockedURI).origin}catch{return}if(blocked===origin&&event.effectiveDirective==='connect-src'&&event.disposition==='enforce')poll()};
const timer=setTimeout(()=>finish(false),5000);
addEventListener('securitypolicyviolation',onViolation);
fetch(origin+'/__native-csp-probe__').then(()=>finish(false)).catch(()=>{});""",
                "args": [],
            },
        )
        require(csp_probe is True, "Enforced candidate-resource CSP control was not observed")
        record["cspProbeObserved"] = True
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
        record["status"] = "pending-parent-validation"
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        record["error"] = str(exc)[:1000]
        if isinstance(exc, WebDriverHTTPError):
            record["driverFailure"] = exc.diagnostic
        raise
    finally:
        finalize_observation(driver, record, evidence_path)
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
