"""Verify frozen startup, packaged version, and bundled SPA without real services."""

import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


def stop_process(process: subprocess.Popen[bytes]) -> None:
    """Release both the frozen launcher and its child before removing cwd."""
    if sys.platform == "win32":
        # Signal the whole frozen process group so its child releases cwd.
        process.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def main() -> None:
    """Start the binary on loopback and verify its packaged runtime assets."""
    binary = Path(sys.argv[1]).resolve()
    expected_version = Path("VERSION").read_text(encoding="utf-8").strip()
    subprocess.run([str(binary), "--help"], check=True, timeout=60)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    environment = dict(os.environ)
    environment.update(
        HOST="127.0.0.1",
        MQTT_HOST="",
        GATEWAY_ENABLED="false",
        SELF_UPDATE_ENABLED="false",
        DASHBOARD_SECRET="",
        INVERTER_DASHBOARD_VERSION="",
    )
    with (
        tempfile.TemporaryDirectory() as directory,
        subprocess.Popen(
            [str(binary), "--port", str(port)],
            cwd=directory,
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        ) as process,
    ):
        try:
            base_url = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 60
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"Frozen application exited with {process.returncode}")
                try:
                    response = httpx.get(f"{base_url}/api/state", timeout=2)
                    response.raise_for_status()
                    state = response.json()
                    break
                except httpx.TransportError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Frozen application did not become ready") from None
                    time.sleep(0.2)
            if state["dashboard_version"] != expected_version:
                raise RuntimeError("Frozen application did not preserve VERSION")
            response = httpx.get(base_url, timeout=5)
            response.raise_for_status()
            html = response.text
            assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html)
            if not assets:
                raise RuntimeError("Frozen application is missing its bundled SPA assets")
            for asset in assets:
                response = httpx.get(f"{base_url}{asset}", timeout=5)
                response.raise_for_status()
                if not response.content:
                    raise RuntimeError(f"Empty packaged asset: {asset}")
        finally:
            stop_process(process)


if __name__ == "__main__":
    main()
