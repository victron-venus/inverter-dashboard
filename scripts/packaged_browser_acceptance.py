#!/usr/bin/env python3
"""Run exact release OCI bytes against isolated peers and capture the served SPA.

The only Docker network is --internal; every application peer is synthetic.
This runner never builds an image, reads local operational config, contacts a
cluster, or accepts arbitrary peer URLs. Browser clients see loopback ports.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import secrets
import subprocess  # nosec B404
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit

from packaged_browser_fixture import CREDENTIAL_ENV, credential_from_env
from prepare_browser_candidate import (
    BUILD_INFO_NAME,
    E2E_NAME,
    MANIFEST_NAME,
    bounded_file,
    digest,
    identity_from_env,
    manifest_inventory,
    validate_bundle,
    verify_receipt,
    version_plan,
)
from qualification_paths import workspace_path

FIXTURE_IMAGE = "python@sha256:05b2b8b732ecd268fee8727a369f936f022d1321b59befd13c30ede22769dcdc"
MAX_OCI = 2 * 1024 * 1024 * 1024
MAX_LOG_CHARS = 256 * 1024
# These are isolated container settings, never workstation listeners or paths.
CONTAINER_LISTEN_HOST = "0.0.0.0"  # nosec B104
FIXTURE_TMPFS = "/tmp:rw,noexec,nosuid,size=16m"  # nosec B108
APPLICATION_TMPFS = "/tmp:rw,noexec,nosuid,size=32m"  # nosec B108
DISABLED_CREDENTIAL = ""


def call(
    *args: str,
    timeout: int = 60,
    check: bool = True,
    environment: dict | None = None,
    combine_logs: bool = False,
) -> str:
    """Use fixed argument vectors, no shell or inherited operational environment."""
    result = subprocess.run(  # nosec B603
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if combine_logs else subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=check,
        env=None if environment is None else {**os.environ, **environment},
    )
    text = result.stdout.strip()
    if combine_logs and len(text) > MAX_LOG_CHARS:
        text = "[Earlier container output truncated]\n" + text[-MAX_LOG_CHARS:]
    return text


def verify_local_docker() -> None:
    """Never let an inherited remote Docker context mutate another machine."""
    if os.environ.get("DOCKER_HOST", "") not in {"", "unix:///var/run/docker.sock"}:
        raise ValueError("Packaged qualification requires the hosted local Docker daemon")
    if os.environ.get("DOCKER_CONTEXT", "") not in {"", "default"}:
        raise ValueError("Remote/nondefault Docker contexts are not allowed")
    contexts = json.loads(call("docker", "context", "inspect"))
    if (
        len(contexts) != 1
        or contexts[0].get("Endpoints", {}).get("docker", {}).get("Host")
        != "unix:///var/run/docker.sock"
    ):
        raise ValueError("Docker endpoint must be the local hosted Unix socket")


def record_container_logs(names: list[str], output: Path, credential: str | None) -> None:
    """Diagnostics are best effort and must never prevent resource teardown."""
    for name in names:
        try:
            # Docker emits application stderr on its own stderr stream. Merge both
            # streams even on failure; bound retrieved lines and retained text.
            content = call("docker", "logs", "--tail", "1000", name, check=False, combine_logs=True)
            if credential:
                content = content.replace(credential, "[redacted fixture credential]")
            (output / f"{name}.log").write_text(content)
        except (OSError, subprocess.SubprocessError) as error:
            with suppress(OSError):
                error_path = output / f"{name}-log-error.txt"
                error_path.write_text(f"{type(error).__name__}\n")


def cleanup_owned(
    names: list[str],
    network: str,
    image: str,
    output: Path,
    *,
    root: Path | None = None,
    credential: str | None = None,
) -> None:
    """Attempt every cleanup independently, including when log collection fails."""
    output = workspace_path(output, root=root, kind="directory")
    if not re.fullmatch(r"browser-qa-[a-f0-9]{16}", network) or image != network + ":candidate":
        raise ValueError("Cleanup can only target this runner's UUID-owned resources")
    if len(names) != len(set(names)) or any(
        name not in {network + suffix for suffix in ("-mqtt", "-igw", "-fixtures")}
        for name in names
    ):
        raise ValueError("Cleanup container does not belong to this runner")
    errors = []
    record_container_logs(names, output, credential)
    commands = [("docker", "rm", "-f", name) for name in names]
    commands += [("docker", "network", "rm", network), ("docker", "image", "rm", image)]
    for command in commands:
        try:
            call(*command, timeout=30)
        except subprocess.CalledProcessError as error:
            detail = error.stderr or ""
            if not any(
                marker in detail for marker in ("No such container", "No such image", "not found")
            ):
                errors.append(f"{command[1]}: {detail[-1000:]}")
        except (OSError, subprocess.TimeoutExpired) as error:
            errors.append(f"{command[1]}: {type(error).__name__}")
    if errors:
        with suppress(OSError):
            (output / "cleanup-errors.log").write_text("\n".join(errors) + "\n")
        raise RuntimeError("Owned fixture cleanup incomplete; see cleanup-errors.log")


def hash_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never send fixture credentials or capture requests to another endpoint."""

    def redirect_request(self, _request, _response, _code, _message, _headers, _url):
        raise ValueError("Packaged frontend must not redirect")


def fetch(base: str, route: str, limit: int = 30 * 1024 * 1024, *, credential: str) -> bytes:
    parsed = urlsplit(base)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or not parsed.port
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Packaged HTTP capture may only use a loopback fixture origin")
    if not route.startswith("/") or route.startswith("//") or "\\" in route:
        raise ValueError("Invalid packaged asset route")
    request = urllib.request.Request(
        base + route, headers={"Authorization": f"Bearer {credential}"}
    )
    # Ignore workstation proxies; the only accepted base is constructed loopback.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=15) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Packaged response exceeds bound")
    return data


def loopback_port(container: str, port: int) -> str:
    value = call("docker", "port", container, f"{port}/tcp")
    if not value.startswith("127.0.0.1:") or not value.split(":")[-1].isdigit():
        raise ValueError("Fixture port is not bound exclusively to loopback")
    return "http://" + value


def wait_ready(base: str, transport: str, credential: str) -> None:
    deadline = time.monotonic() + 60
    last_failure = "source not ready"
    while time.monotonic() < deadline:
        try:
            state = json.loads(fetch(base, "/api/state", 2_000_000, credential=credential))
            if state.get("data_source") == transport and (
                state.get("mqtt_connected") or state.get("gateway_connected")
            ):
                return
        except (OSError, ValueError) as error:
            # Startup can refuse a connection or return an incomplete snapshot.
            # Retry only within the fixed 60-second deadline, retaining a safe
            # error class rather than credentials, URLs or response contents.
            last_failure = type(error).__name__
        time.sleep(0.25)
    raise TimeoutError(
        f"Packaged application did not establish its synthetic source: {last_failure}"
    )


def config_files(directory: Path, credential: str) -> None:
    """Generate ephemeral fixture-only HA config, never copy local_config.py."""
    (directory / "local_config.py").write_text(
        f"HA_URL = 'http://fixtures:8090'\nHA_TOKEN = {credential!r}\n"
        "HA_DIRECT_CONTROLS = True\nHA_POLL_INTERVAL_SEC = 0.5\n"
        "HA_BOOLEAN_ENTITIES = {}\nHA_SENSOR_ENTITIES = {}\nHA_APPLIANCE_ENTITIES = {}\n"
        "HA_FILTERED_ENTITIES = {}\nHA_SWITCH_LABELS = {}\n"
        "HA_SWITCH_ENTITIES = {'fixture': {'entity': 'switch.fixture_light', 'label': 'Fixture light'}}\n"
    )
    (directory / "config.yaml").write_text(
        "homeassistant:\n  url: http://fixtures:8090\n"
        f"  token: {credential}\n  direct_controls: true\n  poll_interval_seconds: 0.5\n"
        "  switch_entities:\n    fixture:\n      entity: switch.fixture_light\n      label: Fixture light\n"
    )
    call(
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "1",
        "-subj",
        "/CN=fixtures",
        "-addext",
        "subjectAltName=DNS:fixtures",
        "-keyout",
        str(directory / "fixture.key"),
        "-out",
        str(directory / "fixture.crt"),
    )


def app_environment(transport: str, credential: str) -> dict[str, str]:
    # Empty credentials deliberately disable live credentials; hosts are container-only.
    return {
        "MQTT_HOST": "fixtures" if transport == "mqtt" else "",
        "MQTT_PORT": "1883",
        "MQTT_TLS": "false",
        "MQTT_USERNAME": "",
        "MQTT_PASSWORD": DISABLED_CREDENTIAL,
        "MQTT_RECONNECT_MIN": "0.5",
        "MQTT_RECONNECT_MAX": "2",
        "CERBO_PORTAL_ID": "browserqa",
        "GATEWAY_ENABLED": str(transport == "igw").lower(),
        "GATEWAY_URL": "https://fixtures:8443" if transport == "igw" else "",
        "GATEWAY_API_TOKEN": credential if transport == "igw" else "",
        "GATEWAY_ACCESS_CLIENT_ID": "",
        "GATEWAY_ACCESS_CLIENT_SECRET": DISABLED_CREDENTIAL,
        "GATEWAY_POLL_INTERVAL": "0.5",
        "DASHBOARD_SECRET": credential,
        "SELF_UPDATE_ENABLED": "false",
        "WEB_PUSH_ENABLED": "false",
        "CAMERA_TOPIC": "",
        "WEB_HOST": CONTAINER_LISTEN_HOST,
        "HOST": CONTAINER_LISTEN_HOST,
        "WEB_PORT": "8080",
        "INVERTER_DASHBOARD_CONFIG": "/qa-config",
        "INVERTER_DASHBOARD_SETTINGS_FILE": "/qa-state/settings.json",
        "SSL_CERT_FILE": "/qa-config/fixture.crt",
        "HTTP_PROXY": "",
        "HTTPS_PROXY": "",
        "ALL_PROXY": "",
    }


def capture_frontend(
    base: str, root: Path, application: dict, credential: str
) -> tuple[dict[str, bytes], dict]:
    manifest_data = bounded_file(root / application["manifest"], 2_000_000)
    manifest = json.loads(manifest_data)
    inventory = manifest_inventory(manifest, application["manifestKind"])
    files = {MANIFEST_NAME: manifest_data}
    served = {}
    for name, expected in inventory.items():
        if name == BUILD_INFO_NAME and application["manifestKind"] == "go":
            # Embedded build metadata has no HTTP route. It is not executable.
            data = bounded_file(root / application["distribution"] / name, 2_000_000)
        else:
            route = "/" if name == "index.html" else "/" + name
            if name == BUILD_INFO_NAME:
                route = "/static/build-info.json"
            data = fetch(base, route, credential=credential)
            served[name] = digest(data)
        if digest(data) != expected:
            raise ValueError(f"Packaged frontend differs from provenance: {name}")
        files["frontend/" + name] = data
    return files, served


def load_candidate(root: Path, artifacts: Path, producer: dict) -> tuple:
    """Validate fixed release metadata and exact bounded OCI before Docker import."""
    application = json.loads((root / ".github/browser-application.json").read_text())
    backend = application["manifestKind"]
    archive_name = (
        "inverter-dashboard-container.oci.tar"
        if backend == "python"
        else "inverter-dashboard-go-container.oci.tar"
    )
    archive = artifacts / archive_name
    if (
        any(p.is_symlink() for p in (archive, *archive.parents))
        or not archive.is_file()
        or archive.stat().st_size > MAX_OCI
    ):
        raise ValueError("OCI must be a bounded regular file")
    receipt_data = bounded_file(artifacts / "release-inputs-container.json", 2_000_000)
    receipt = json.loads(receipt_data)
    policy = json.loads((root / ".release-policy.json").read_text())
    plan_data = bounded_file(root / ".release-plan.json", 2_000_000)
    plan = version_plan.validate_plan(json.loads(plan_data), policy, producer["source_sha"])
    plan_sha = version_plan.plan_digest(plan)
    oci_sha = hash_file(archive)
    verify_receipt(receipt, producer, archive_name, oci_sha, archive.stat().st_size)
    return application, archive, receipt_data, policy, plan_data, plan, plan_sha, oci_sha


def verified_loaded_image(image: str, producer: dict, plan: dict) -> dict:
    """The runtime under test must be the planned source and package projection."""
    inspected = json.loads(call("docker", "image", "inspect", image))[0]
    labels = inspected["Config"].get("Labels", {})
    if labels.get("org.opencontainers.image.revision") != producer["source_sha"]:
        raise ValueError("Loaded OCI source label mismatch")
    if labels.get("org.opencontainers.image.version") != version_plan.projections(plan)["package"]:
        raise ValueError("Loaded OCI lacks version identity")
    return inspected


def start_application(
    backend: str, transport: str, config: Path, name: str, network: str, image: str, credential: str
) -> str:
    """Start the imported package with synthetic peers and no inherited secrets."""
    command = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "--network",
        network,
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--memory=768m",
        "--cpus=2",
        "--pids-limit=128",
        "-p",
        "127.0.0.1::8080",
        "--tmpfs",
        APPLICATION_TMPFS,
        "--tmpfs",
        "/qa-state:rw,noexec,nosuid,mode=1777,size=16m",
        "-v",
        f"{config}:/qa-config:ro",
    ]
    if backend == "go":
        command += ["-v", f"{config / 'config.yaml'}:/app/config.yaml:ro"]
    environment = app_environment(transport, credential)
    command.extend(item for key in environment for item in ("-e", key))
    call(*command, image, "--port", "8080", environment=environment)
    return loopback_port(name, 8080)


def run_browser(
    root: Path,
    base: str,
    fixture_base: str,
    transport: str,
    run_file: Path,
    oci_sha: str,
    backend: str,
    credential: str,
) -> list[dict]:
    """Execute real-browser transport scenarios and redact diagnostic credentials."""
    try:
        call(
            "node",
            str(root / "scripts/packaged_browser_e2e.cjs"),
            base,
            fixture_base,
            transport,
            str(run_file.resolve()),
            oci_sha,
            backend,
            timeout=300,
            environment={CREDENTIAL_ENV: credential},
        )
    except subprocess.CalledProcessError as error:
        run_file.with_suffix(".failure.log").write_text(
            f"{error.stdout or ''}{error.stderr or ''}".replace(
                credential, "[redacted fixture credential]"
            )
        )
        raise
    return json.loads(run_file.read_text())["runs"]


def execute(root: Path, artifacts: Path, output: Path, artifact_id: int) -> None:
    """Own and tear down only UUID-labelled containers and temporary fixtures."""
    artifacts = workspace_path(artifacts, root=root, kind="directory", must_exist=True)
    output = workspace_path(output, root=root, kind="directory", new=True)
    if type(artifact_id) is not int or artifact_id <= 0:
        raise ValueError("Exact positive source artifact ID required")
    credential = credential_from_env({CREDENTIAL_ENV: secrets.token_urlsafe(32)})
    producer = identity_from_env()
    verify_local_docker()
    application, archive, receipt_data, policy, plan_data, plan, plan_sha, oci_sha = load_candidate(
        root, artifacts, producer
    )
    backend = application["manifestKind"]
    archive_name = archive.name
    owned = "browser-qa-" + uuid.uuid4().hex[:16]
    image, network, fixture_name = f"{owned}:candidate", owned, f"{owned}-fixtures"
    output.mkdir(parents=True)
    app_names, runtime_runs, files, served = [], [], {}, {}
    with tempfile.TemporaryDirectory(prefix="packaged-browser-") as temporary:
        config = Path(temporary)
        config.chmod(0o755)
        config_files(config, credential)
        try:
            # Copy only the selected OCI platform into Docker; never rebuild it.
            call(
                "skopeo",
                "copy",
                "--override-os",
                "linux",
                "--override-arch",
                "amd64",
                "oci-archive:" + str(archive.resolve()),
                "docker-daemon:" + image,
                timeout=180,
            )
            inspected = verified_loaded_image(image, producer, plan)
            labels = inspected["Config"].get("Labels", {})
            call("docker", "pull", FIXTURE_IMAGE, timeout=180)
            call(
                "docker",
                "network",
                "create",
                "--internal",
                "--label",
                "dashboard.packaged-qa=" + owned,
                network,
            )
            info = json.loads(call("docker", "network", "inspect", network))[0]
            if info.get("Internal") is not True:
                raise ValueError("Fixture network must deny external routing")
            # /tmp below is a bounded container tmpfs, never a host filesystem path.
            call(
                "docker",
                "run",
                "-d",
                "--name",
                fixture_name,
                "--network",
                network,
                "--network-alias",
                "fixtures",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--memory=128m",
                "--cpus=1",
                "--pids-limit=64",
                "-p",
                "127.0.0.1::8090",
                "--tmpfs",
                FIXTURE_TMPFS,
                "-v",
                f"{root / 'scripts/packaged_browser_fixture.py'}:/fixture.py:ro",
                "-v",
                f"{config}:/qa-config:ro",
                "-e",
                CREDENTIAL_ENV,
                FIXTURE_IMAGE,
                "python",
                "/fixture.py",
                "--cert",
                "/qa-config/fixture.crt",
                "--key",
                "/qa-config/fixture.key",
                environment={CREDENTIAL_ENV: credential},
            )
            fixture_base = loopback_port(fixture_name, 8090)
            for transport in ("mqtt", "igw"):
                name = f"{owned}-{transport}"
                app_names.append(name)
                base = start_application(
                    backend, transport, config, name, network, image, credential
                )
                wait_ready(base, transport, credential)
                current, current_served = capture_frontend(base, root, application, credential)
                if files and current != files:
                    raise ValueError("Served frontend changed with transport")
                files, served = current, current_served
                run_file = output / f"runtime-{transport}.json"
                runtime_runs.extend(
                    run_browser(
                        root, base, fixture_base, transport, run_file, oci_sha, backend, credential
                    )
                )
                call("docker", "stop", "--time", "5", name)
            if any(
                run["backendVersion"] != labels["org.opencontainers.image.version"]
                for run in runtime_runs
            ):
                raise ValueError("Running backend version differs from OCI label")
            e2e = {
                "schema": 1,
                "producer": producer,
                "ociSha256": oci_sha,
                "containerArtifactId": artifact_id,
                "dockerImageId": inspected["Id"],
                "platform": "linux/amd64",
                "networkInternal": True,
                "fixtureImage": FIXTURE_IMAGE,
                "planSha256": plan_sha,
                "runs": runtime_runs,
            }
            files[E2E_NAME] = (json.dumps(e2e, sort_keys=True, indent=2) + "\n").encode()
            files["release-inputs-container.json"] = receipt_data
            files["release-plan.json"] = plan_data
            inventory = manifest_inventory(json.loads(files[MANIFEST_NAME]), backend)
            metadata = {
                "schema": 1,
                "producer": producer,
                "containerArtifactId": artifact_id,
                "planSha256": plan_sha,
                "ociArchive": archive_name,
                "ociSha256": oci_sha,
                "ociSize": archive.stat().st_size,
                "receiptSha256": digest(receipt_data),
                "manifestKind": backend,
                "manifestSha256": digest(files[MANIFEST_NAME]),
                "frontendFiles": inventory,
                "servedFiles": served,
                "sourceOnlyMetadata": [BUILD_INFO_NAME] if backend == "go" else [],
                "e2eSha256": digest(files[E2E_NAME]),
            }
            files["candidate-provenance.json"] = (
                json.dumps(metadata, sort_keys=True, indent=2) + "\n"
            ).encode()
            validate_bundle(files, producer, policy)
            with tarfile.open(output / "browser-candidate.tar.gz", "w:gz") as bundle:
                for name, data in sorted(files.items()):
                    entry = tarfile.TarInfo(name)
                    entry.size, entry.mode = len(data), 0o644
                    bundle.addfile(entry, io.BytesIO(data))
            candidate_sha = hash_file(output / "browser-candidate.tar.gz")
            (output / "browser-candidate.sha256").write_text(
                f"{candidate_sha}  browser-candidate.tar.gz\n"
            )
            (output / E2E_NAME).write_bytes(files[E2E_NAME])
        finally:
            cleanup_owned(
                app_names + [fixture_name], network, image, output, root=root, credential=credential
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact-id", type=int, required=True)
    args = parser.parse_args()
    execute(Path(__file__).resolve().parents[1], args.artifacts, args.output, args.artifact_id)


if __name__ == "__main__":
    main()
