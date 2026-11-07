#!/usr/bin/env python3
"""Validate a same-run, OCI-served browser candidate; never rebuild its frontend."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tarfile
from pathlib import Path, PurePosixPath

import version_plan
from version_receipt import verify_producer_identity

MAX_ARCHIVE = 150 * 1024 * 1024
MAX_UNPACKED = 300 * 1024 * 1024
PACKAGED_SCENARIOS = {
    "real-http-auth-and-websocket-token-rejection",
    "real-transport-native-banner-preserves-source-time",
    "unknown-home-target-rejected-without-publication",
    "real-correlated-command-routes-once-to-selected-transport",
    "configured-home-only-real-ha-service-route",
    "cross-origin-websocket-rejected-even-with-valid-token",
}
MQTT_SCENARIO = "reconnect-retires-authority-and-source-event-time"


def digest(data: bytes) -> str:
    """Return the content identity used by all candidate receipts."""
    return hashlib.sha256(data).hexdigest()


def safe_name(name: str) -> str:
    """Accept portable relative file names only, before writing any file."""
    if not isinstance(name, str) or not name or len(name) > 240:
        raise ValueError("Invalid candidate path")
    parts = name.split("/")
    if (
        name.startswith("/")
        or "\\" in name
        or ":" in name
        or any(p in {"", ".", ".."} or p.endswith((".", " ")) for p in parts)
        or any(ord(c) < 32 for c in name)
    ):
        raise ValueError("Unsafe candidate path")
    return PurePosixPath(name).as_posix()


def bounded_file(path: Path, limit: int) -> bytes:
    """Do not follow symlinks in artifact input paths."""
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ValueError("Candidate input must be a regular file without symlinks")
    if path.stat().st_size > limit:
        raise ValueError("Candidate input exceeds size limit")
    return path.read_bytes()


def identity_from_env() -> dict:
    """Release consumers require independently supplied Actions identity."""
    result = {
        "repository": os.environ.get("GITHUB_REPOSITORY", ""),
        "source_sha": os.environ.get("GITHUB_SHA", ""),
    }
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", result["repository"]):
        raise ValueError("Missing candidate repository")
    if not re.fullmatch(r"[0-9a-f]{40}", result["source_sha"]):
        raise ValueError("Missing candidate source SHA")
    for key, env in (("run_id", "GITHUB_RUN_ID"), ("run_attempt", "GITHUB_RUN_ATTEMPT")):
        value = os.environ.get(env, "")
        if not re.fullmatch(r"[1-9][0-9]*", value):
            raise ValueError("Missing candidate run identity")
        result[key] = int(value)
    return result


def manifest_inventory(manifest: dict, kind: str) -> dict[str, str]:
    """Read the existing committed frontend provenance without rewriting it."""
    if manifest.get("repository") != "https://github.com/victron-venus/inverter-dashboard-vue":
        raise ValueError("Unexpected frontend producer")
    if kind == "python":
        items = [(x["path"], x["sha256"]) for x in manifest["files"]]
    elif kind == "go":
        items = list(manifest["sha256"].items())
    else:
        raise ValueError("Unknown frontend provenance format")
    result = {}
    for name, value in items:
        name = safe_name(name)
        if name.casefold() in {x.casefold() for x in result} or not re.fullmatch(
            r"[0-9a-f]{64}", value
        ):
            raise ValueError("Invalid frontend inventory")
        result[name] = value
    if not result or "index.html" not in result or len(result) > 1000:
        raise ValueError("Missing or excessive frontend inventory")
    return result


def read_bundle(archive: Path) -> dict[str, bytes]:
    """Bound extraction and reject duplicates, links and tar path tricks."""
    bounded_file(archive, MAX_ARCHIVE)
    result, seen, total = {}, set(), 0
    with tarfile.open(archive, "r:gz") as bundle:
        for entry in bundle:
            name = safe_name(entry.name)
            total += entry.size
            if not entry.isfile() or entry.size < 0 or total > MAX_UNPACKED or len(result) >= 1100:
                raise ValueError("Invalid candidate archive member or size")
            if name.casefold() in seen:
                raise ValueError("Duplicate candidate archive member")
            seen.add(name.casefold())
            stream = bundle.extractfile(entry)
            if stream is None:
                raise ValueError("Unreadable candidate archive member")
            result[name] = stream.read()
    return result


def verify_receipt(receipt: dict, producer: dict, archive_name: str, sha: str, size: int) -> None:
    """Bind the runtime OCI bytes to the frozen release build receipt."""
    verify_producer_identity(receipt, producer)
    if receipt.get("source_sha") != producer["source_sha"]:
        raise ValueError("Candidate belongs to another producer or source")
    entries = [x for x in receipt.get("artifacts", []) if x.get("name") == archive_name]
    if len(entries) != 1 or entries[0].get("sha256") != sha or entries[0].get("size") != size:
        raise ValueError("OCI differs from its release receipt")


def validate_bundle(
    files: dict[str, bytes], producer: dict, policy: dict | None = None
) -> tuple[dict, dict]:
    """Require complete, positive packaged evidence and exact frontend hashes."""
    metadata = json.loads(files["candidate-provenance.json"])
    if type(metadata.get("schema")) is not int or metadata["schema"] != 1:
        raise ValueError("Unknown candidate schema")
    manifest = json.loads(files["manifest.json"])
    receipt_data = files["release-inputs-container.json"]
    receipt = json.loads(receipt_data)
    plan = version_plan.validate_plan(
        json.loads(files["release-plan.json"]), policy, producer["source_sha"]
    )
    plan_sha = version_plan.plan_digest(plan)
    inputs = receipt.get("files")
    if not isinstance(inputs, list) or not inputs:
        raise ValueError("Missing frozen build inputs")
    if (
        receipt.get("plan_sha256") != plan_sha
        or metadata.get("planSha256") != plan_sha
        or receipt.get("effective_inputs_sha256") != version_plan.effective_inputs_digest(inputs)
    ):
        raise ValueError("Candidate frozen plan/input digest mismatch")
    if policy is not None and {x["path"] for x in inputs} != {
        x["path"] for x in policy["versioning"]["files"]
    }:
        raise ValueError("Candidate build inputs do not cover the release policy")
    verify_producer_identity(metadata, producer)
    if metadata.get("producer") != producer:
        raise ValueError("Candidate producer/run/attempt mismatch")
    verify_receipt(
        receipt, producer, metadata["ociArchive"], metadata["ociSha256"], metadata["ociSize"]
    )
    if metadata.get("receiptSha256") != digest(receipt_data):
        raise ValueError("Candidate receipt hash mismatch")
    inventory = manifest_inventory(manifest, metadata["manifestKind"])
    expected = {
        "candidate-provenance.json",
        "manifest.json",
        "release-inputs-container.json",
        "packaged-e2e.json",
        "release-plan.json",
    }
    expected.update("frontend/" + name for name in inventory)
    if set(files) != expected:
        raise ValueError("Candidate file inventory mismatch")
    for name, sha in inventory.items():
        if digest(files["frontend/" + name]) != sha:
            raise ValueError("Candidate frontend hash mismatch")
    if metadata.get("frontendFiles") != inventory or metadata.get("manifestSha256") != digest(
        files["manifest.json"]
    ):
        raise ValueError("Candidate manifest mismatch")
    source_only = metadata.get("sourceOnlyMetadata")
    if source_only != (["build-info.json"] if metadata["manifestKind"] == "go" else []):
        raise ValueError("Unexpected unserved candidate data")
    served = {k: v for k, v in inventory.items() if k not in source_only}
    if metadata.get("servedFiles") != served:
        raise ValueError("Not all executable frontend bytes were served by the package")
    e2e = json.loads(files["packaged-e2e.json"])
    if type(e2e.get("schema")) is not int or e2e["schema"] != 1:
        raise ValueError("Unknown packaged E2E schema")
    verify_producer_identity(e2e, producer)
    container_id = metadata.get("containerArtifactId")
    if (
        type(container_id) is not int
        or container_id <= 0
        or type(e2e.get("containerArtifactId")) is not int
        or e2e["containerArtifactId"] != container_id
    ):
        raise ValueError("Packaged container artifact lineage mismatch")
    if e2e.get("producer") != producer or e2e.get("ociSha256") != metadata["ociSha256"]:
        raise ValueError("Packaged E2E identity mismatch")
    expected_runs = {
        (mode, engine) for mode in ("mqtt", "igw") for engine in ("chromium", "firefox", "webkit")
    }
    runs = e2e.get("runs", [])
    actual = {(x.get("transport"), x.get("engine")) for x in runs}
    if len(runs) != 6 or actual != expected_runs or any(x.get("status") != "passed" for x in runs):
        raise ValueError("Packaged E2E did not pass every engine and transport")
    if e2e.get("planSha256") != plan_sha or e2e.get("networkInternal") is not True:
        raise ValueError("Packaged E2E lacks frozen plan or network isolation")
    for run in runs:
        verify_producer_identity(run, producer)
        expected_scenarios = PACKAGED_SCENARIOS | (
            {MQTT_SCENARIO} if run["transport"] == "mqtt" else set()
        )
        scenarios = run.get("scenarios", [])
        if (
            set(scenarios) != expected_scenarios
            or len(scenarios) != len(expected_scenarios)
            or type(run.get("testsPassed")) is not int
            or run.get("testsPassed") != len(expected_scenarios)
            or type(run.get("testsFailed")) is not int
            or run.get("testsFailed") != 0
            or run.get("producer") != producer
            or run.get("ociSha256") != metadata["ociSha256"]
            or run.get("backendVersion") != version_plan.projections(plan)["package"]
            or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", run.get("browserVersion", ""))
        ):
            raise ValueError("Incomplete or unbound packaged E2E scenario evidence")
    if metadata.get("e2eSha256") != digest(files["packaged-e2e.json"]):
        raise ValueError("Packaged E2E hash mismatch")
    return metadata, inventory


def prepare(
    artifacts: Path, destination: Path, diagnostics: Path, producer: dict, artifact_id: int
) -> dict:
    """Unpack a validated candidate into a new owned directory only."""
    if type(artifact_id) is not int or artifact_id <= 0:
        raise ValueError("Exact candidate artifact ID required")
    if destination.exists() or any(p.is_symlink() for p in (destination, *destination.parents)):
        raise ValueError("Candidate destination must be new and without symlinks")
    archive = artifacts / "browser-candidate.tar.gz"
    data = bounded_file(archive, MAX_ARCHIVE)
    checksum = bounded_file(artifacts / "browser-candidate.sha256", 200).decode().strip()
    if checksum != f"{digest(data)}  browser-candidate.tar.gz":
        raise ValueError("Candidate checksum mismatch")
    files = read_bundle(archive)
    metadata, inventory = validate_bundle(
        files,
        producer,
        json.loads((Path(__file__).resolve().parents[1] / ".release-policy.json").read_text()),
    )
    destination.mkdir(parents=True)
    try:
        for name, content in files.items():
            output = destination / name
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("xb") as stream:
                stream.write(content)
    except BaseException:
        shutil.rmtree(destination)
        raise
    identity = {
        "repository": producer["repository"],
        "sourceSha": producer["source_sha"],
        "runId": producer["run_id"],
        "runAttempt": producer["run_attempt"],
        "artifactId": artifact_id,
        "candidateArtifactId": artifact_id,
        "archiveSha256": digest(data),
        "candidateSha256": digest(data),
        "containerArtifactId": metadata["containerArtifactId"],
        "ociSha256": metadata["ociSha256"],
        "receiptSha256": metadata["receiptSha256"],
        "producer": producer,
        "planSha256": metadata["planSha256"],
        "manifestKind": metadata["manifestKind"],
        "distribution": str((destination / "frontend").resolve()),
        "manifestPath": str((destination / "manifest.json").resolve()),
        "frontendFiles": inventory,
        "servedFiles": metadata["servedFiles"],
        "sourceOnlyMetadata": metadata["sourceOnlyMetadata"],
        "releaseReceipt": True,
    }
    diagnostics.mkdir(parents=True, exist_ok=True)
    (diagnostics / "candidate.json").write_text(json.dumps(identity, indent=2) + "\n")
    return identity


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    prepare(
        args.artifacts,
        args.destination,
        Path(os.environ["CI_BROWSER_DIAGNOSTICS"]),
        identity_from_env(),
        int(os.environ["CI_BROWSER_CANDIDATE_ARTIFACT_ID"]),
    )


if __name__ == "__main__":
    main()
