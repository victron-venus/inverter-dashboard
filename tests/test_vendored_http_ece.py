"""Verify the reviewed wheel is source-identical, licensed and hash-bound."""

import hashlib
import json
import tomllib
import zipfile
from pathlib import Path

from scripts.rebuild_http_ece_wheel import verify_runtime

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "http-ece"


def test_vendored_runtime_exact_upstream_bytes_and_license():
    manifest = json.loads((VENDOR / "provenance.json").read_text())
    for filename, digest in manifest["artifactSHA256"].items():
        assert hashlib.sha256((VENDOR / filename).read_bytes()).hexdigest() == digest
    wheel = VENDOR / "http_ece-1.2.1-py2.py3-none-any.whl"
    assert verify_runtime(VENDOR / "http_ece-1.2.1.tar.gz", wheel) == manifest["runtimeSourceFiles"]
    with zipfile.ZipFile(wheel) as built:
        assert (
            built.read("http_ece-1.2.1.dist-info/licenses/LICENSE")
            == (VENDOR / "LICENSE").read_bytes()
        )
        assert "Root-Is-Purelib: true" in built.read("http_ece-1.2.1.dist-info/WHEEL").decode()
        assert not any(name.endswith((".so", ".pyd", ".dll")) for name in built.namelist())


def test_uv_lock_and_docker_bind_exact_reviewed_wheel():
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    package = next(item for item in lock["package"] if item["name"] == "http-ece")
    assert package["source"] == {"path": "vendor/http-ece/http_ece-1.2.1-py2.py3-none-any.whl"}
    expected = json.loads((VENDOR / "provenance.json").read_text())["artifactSHA256"][
        "http_ece-1.2.1-py2.py3-none-any.whl"
    ]
    assert package["wheels"][0]["hash"] == "sha256:" + expected
    assert (
        "COPY vendor/http-ece/http_ece-1.2.1-py2.py3-none-any.whl ./vendor/http-ece/"
        in (ROOT / "Dockerfile").read_text()
    )
