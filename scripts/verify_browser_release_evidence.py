#!/usr/bin/env python3
"""Preserve exact packaged browser qualification as a release payload.

Inputs must come from the authenticated current Actions run. This verifier checks
their content and identity; it cannot authenticate arbitrary operator-supplied
JSON as GitHub evidence. Native OS acceptance remains a separate requirement.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import tarfile
from pathlib import Path

from prepare_browser_candidate import bounded_file, prepare
from qualification_paths import workspace_path

RESULT_JSON = "result.json"
RUNTIME_JSON = "runtime.json"
BROWSER_VERSION = re.compile(r"\d+(?:\.\d+){1,3}")

PWA_SCENARIOS = {
    "secure-context-manifest-and-exact-worker",
    "startup-does-not-request-permission-or-subscribe",
    "denied-permission-does-not-register",
    "granted-permission-registers-ui-fixture-subscription",
    "worker-generation-rejects-stale-enable",
    "worker-update-preserves-revoked-settings",
    "disable-removes-fixture-and-browser-subscription",
    "worker-does-not-cache-assets-or-handle-offline-controls",
    "worker-unregister-removes-registration",
}
SAFARI_SMOKE_SCENARIOS = {
    "branded-browser-secure-runtime",
    "candidate-telemetry-hydrates",
    "settings-and-notification-section-open",
    "native-served-worker-matches-candidate",
}


def require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load(path: Path) -> tuple[dict, bytes]:
    raw = bounded_file(path, 2_000_000)
    value = json.loads(raw)
    require(isinstance(value, dict), "Evidence must be a JSON object")
    return value, raw


def candidate_identity(candidate: dict) -> dict:
    keys = (
        "repository",
        "sourceSha",
        "runId",
        "runAttempt",
        "artifactId",
        "containerArtifactId",
        "archiveSha256",
        "ociSha256",
        "receiptSha256",
        "planSha256",
    )
    result = {key: candidate.get(key) for key in keys}
    require(all(value is not None for value in result.values()), "Incomplete candidate identity")
    for key in ("runId", "runAttempt", "artifactId", "containerArtifactId"):
        require(type(result[key]) is int and result[key] > 0, f"Invalid candidate identity: {key}")
    for key in ("archiveSha256", "ociSha256", "receiptSha256", "planSha256"):
        require(
            isinstance(result[key], str) and re.fullmatch(r"[a-f0-9]{64}", result[key]),
            f"Invalid candidate digest: {key}",
        )
    return result


def verify_target(
    row: dict, folder: Path, binding: dict, candidate: dict, scenarios: list[str], contract: dict
) -> dict[str, bytes]:
    values, originals = {}, {}
    for name in ("target.json", RUNTIME_JSON, RESULT_JSON, "candidate.json"):
        values[name], originals[name] = load(folder / name)
    # The pinned reusable producer materializes the default channel in every row.
    expected_row = dict(row, channel=row.get("channel", ""))
    require(values["target.json"] == expected_row, "Browser target differs from source matrix")
    platform = "darwin"
    if row["os"].startswith("ubuntu-"):
        platform = "linux"
    elif row["os"].startswith("windows-"):
        platform = "win32"
    expected = dict(
        binding,
        target=row["id"],
        engine=row["engine"],
        channel=row.get("channel", ""),
        playwrightVersion=row["playwright"],
        platform=platform,
    )
    for name in (RUNTIME_JSON, RESULT_JSON):
        require(
            all(values[name].get(key) == value for key, value in expected.items()),
            f"Browser source/run/target identity mismatch: {name}",
        )
    runtime, result = values[RUNTIME_JSON], values[RESULT_JSON]
    version = runtime.get("browserVersion")
    require(
        isinstance(version, str) and BROWSER_VERSION.fullmatch(version),
        "Missing actual browser version",
    )
    require(result.get("browserVersion") == version, "Browser version evidence differs")
    if not row.get("channel"):
        require(version == row["browser_version"], "Wrong pinned browser version")
    require(
        type(result.get("testsFailed")) is int and result["testsFailed"] == 0,
        "Failed browser scenarios",
    )
    require(
        type(result.get("testsPassed")) is int and result["testsPassed"] == len(scenarios),
        "Incomplete browser scenario count",
    )
    actual = result.get("scenarios")
    require(
        isinstance(actual, list)
        and len(actual) == len(scenarios)
        and all(isinstance(item, str) for item in actual)
        and set(actual) == set(scenarios),
        "Missing, duplicate or unexpected scenario",
    )
    require(result.get("scenarioContract") == contract, "Browser scenario contract changed")
    identity = candidate_identity(candidate)
    for value in (values["candidate.json"], result.get("candidate", {})):
        require(candidate_identity(value) == identity, "Browser candidate bytes differ")
        require(
            value.get("frontendFiles") == candidate["frontendFiles"],
            "Browser frontend inventory differs",
        )
    return originals


def collect(
    candidate: dict,
    matrix: list[dict],
    contract_data: bytes,
    evidence: Path,
    prefix: str,
    channel: str,
) -> dict[str, bytes]:
    require(channel in {"nightly", "beta", "rc"}, "Only candidate channels build evidence")
    require(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", prefix), "Unsafe evidence prefix")
    require(
        len(matrix) == 35 and len({row["id"] for row in matrix}) == 35,
        "Source must declare all 35 browser targets",
    )
    require(
        sum(row.get("tier") == "required" for row in matrix) == 5,
        "Source must declare exactly five baseline targets",
    )
    rows = matrix if channel == "rc" else [row for row in matrix if row["tier"] == "required"]
    contract_source = json.loads(contract_data)
    scenarios = contract_source["scenarios"]
    require(
        scenarios
        and all(isinstance(item, str) for item in scenarios)
        and len(set(scenarios)) == len(scenarios),
        "Invalid scenario contract",
    )
    contract = {
        "id": contract_source["id"],
        "sha256": sha256(contract_data),
        "required": len(scenarios),
    }
    binding = {
        "schema": 1,
        "sourceSha": candidate["sourceSha"],
        "runId": str(candidate["runId"]),
        "runAttempt": str(candidate["runAttempt"]),
        "releaseChannel": channel,
        "candidateArtifactId": str(candidate["artifactId"]),
    }
    names = {f"{prefix}-{row['id']}-{binding['runAttempt']}" for row in rows}
    require(evidence.is_dir() and not evidence.is_symlink(), "Missing evidence directory")
    require(
        {path.name for path in evidence.iterdir()} == names,
        "Missing, reduced, duplicate or unexpected browser target evidence",
    )
    payloads = {"browser-scenarios.json": contract_data}
    for row in rows:
        name = f"{prefix}-{row['id']}-{binding['runAttempt']}"
        originals = verify_target(row, evidence / name, binding, candidate, scenarios, contract)
        payloads.update({f"targets/{row['id']}/{key}": value for key, value in originals.items()})
    verdict = {
        "schema": 1,
        "scope": "packaged-browser-engines",
        **candidate_identity(candidate),
        "releaseChannel": channel,
        "targetCount": len(rows),
        "allDeclaredPlatforms": True,
        "fullVersionMatrix": channel == "rc",
        "nativeDeviceQualified": False,
        "osNotificationDisplayQualified": False,
        "scenarioContract": contract,
        "targets": [row["id"] for row in rows],
        "files": {name: sha256(raw) for name, raw in sorted(payloads.items())},
    }
    payloads["qualification.json"] = (json.dumps(verdict, sort_keys=True, indent=2) + "\n").encode()
    return payloads


def archive(output: Path, payloads: dict[str, bytes], *, root: Path | None = None) -> None:
    # Enforce the boundary at the writer as well as the CLI parser. Only pure
    # unit controls supply another trusted root; the CLI never exposes it.
    output = workspace_path(output, root=root, kind="file", new=True)
    require(
        not output.exists() and not any(p.is_symlink() for p in (output, *output.parents)),
        "Qualification output must be new and unlinked",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with (
        output.open("xb") as raw,
        gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped,
        tarfile.open(fileobj=zipped, mode="w") as bundle,
    ):
        for name, data in sorted(payloads.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o644
            bundle.addfile(info, io.BytesIO(data))


def native_parent_evidence(report: dict) -> None:
    """Only the parent can accept the child after traffic and cleanup checks."""
    require(
        report.get("sessionCleanupSucceeded") is True
        and report.get("sessionCleanupFailed") is not True,
        "Native Safari session cleanup did not succeed",
    )
    parent = report.get("parentValidation", {})
    require(
        parent.get("complete") is True and parent.get("cleanupSucceeded") is True,
        "Native smoke lacks completed parent validation",
    )
    require(
        all(
            type(parent.get(key)) is int and parent[key] == 0
            for key in ("applicationFrames", "protocolErrors", "httpWrites")
        ),
        "Native smoke observed unexpected commands or protocol traffic",
    )
    isolation = report.get("networkIsolation", {})
    require(
        isolation.get("mechanism") == "content-security-policy"
        and isolation.get("scope") == "candidate-document-resources"
        and isolation.get("enforced") is True
        and isolation.get("probeObserved") is True
        and type(isolation.get("probeReports")) is int
        and isolation["probeReports"] == 1,
        "Native smoke lacks measured candidate resource isolation",
    )
    require(
        all(
            type(isolation.get(key)) is int and isolation[key] == 0
            for key in ("candidateViolations", "malformedReports")
        )
        and isinstance(isolation.get("policySha256"), str)
        and re.fullmatch(r"[a-f0-9]{64}", isolation["policySha256"]),
        "Native smoke resource isolation failed",
    )


def native_smoke_evidence(folder: Path, report: dict, candidate: dict) -> bytes:
    """Verify measured Safari/host identity and the original screenshot."""
    require(
        report.get("scope") == "native-smoke" and report.get("nativeComplete") is False,
        "Native smoke must remain partial",
    )
    native_parent_evidence(report)
    require(
        report.get("requestedPlatform") == "macOS" and report.get("runtime") == "native-host",
        "Native smoke used the wrong runtime",
    )
    branded = report.get("driverCapabilities", {})
    require(
        branded.get("browserName", "").lower() == "safari"
        and branded.get("platformName", "").lower() in {"mac", "macos"}
        and isinstance(branded.get("browserVersion"), str)
        and BROWSER_VERSION.fullmatch(branded["browserVersion"]),
        "Native smoke lacks measured Safari runtime identity",
    )
    runtime = report.get("runtimeIdentity", {})
    require(
        runtime.get("secureContext") is True
        and runtime.get("serviceWorker") is True
        and isinstance(runtime.get("userAgent"), str)
        and runtime["userAgent"],
        "Native smoke lacks secure browser context evidence",
    )
    host = report.get("hostIdentity", {})
    require(
        host.get("osName") == "macOS"
        and host.get("architecture") in {"arm64", "x86_64"}
        and isinstance(host.get("osVersion"), str)
        and re.fullmatch(r"\d+(?:\.\d+){1,2}", host["osVersion"])
        and isinstance(host.get("osBuild"), str)
        and re.fullmatch(r"[A-Za-z0-9.]{1,64}", host["osBuild"]),
        "Native smoke lacks measured host OS/build identity",
    )
    require(
        report.get("workerSha256") == candidate["frontendFiles"]["notifications-sw.js"],
        "Native smoke served another worker",
    )
    reference = report.get("screenshot", {})
    require(reference.get("path") == "native-smoke.png", "Unexpected native screenshot path")
    png = bounded_file(folder / "result/native-smoke.png", 20_000_000)
    require(
        png.startswith(b"\x89PNG\r\n\x1a\n") and sha256(png) == reference.get("sha256"),
        "Native screenshot is missing or changed",
    )
    return png


def pwa_engine_evidence(report: dict) -> None:
    """Check engine boundaries without asserting native acceptance."""
    require(
        report.get("scope") == "engine-pwa-isolated"
        and report.get("engine") == "chromium"
        and report.get("serviceWorkers") == "allow"
        and report.get("failures") == [],
        "PWA worker evidence is not the required engine lane",
    )
    require(
        report.get("nativeQualified") is False
        and report.get("previousVersionUpgradeQualified") is False,
        "PWA engine evidence overclaims native or prior-version upgrade acceptance",
    )
    require(
        isinstance(report.get("browserVersion"), str)
        and BROWSER_VERSION.fullmatch(report["browserVersion"])
        and report.get("permissionMechanism") == "browser-test-override"
        and report.get("subscriptionMechanism") == "isolated-ui-fixture"
        and report.get("tlsTrust") == "isolated-self-signed",
        "PWA engine runtime and synthetic boundaries must be explicit",
    )
    require(
        report.get("browserContextMode") == "isolated-persistent-profile",
        "PWA qualification requires an isolated persistent profile",
    )


def additional_evidence(folder: Path, candidate: dict, native: bool) -> dict[str, bytes]:
    """Keep engine/Safari smoke evidence without upgrading it to native acceptance."""
    identity, identity_raw = load(folder / "identity/candidate.json")
    require(
        candidate_identity(identity) == candidate_identity(candidate),
        "Additional lane candidate differs",
    )
    name = "native-smoke.json" if native else RESULT_JSON
    report, raw = load(folder / "result" / name)
    for value in (identity, report):
        require(
            value.get("frontendFiles") == candidate["frontendFiles"],
            "Additional lane frontend inventory differs from the release candidate",
        )
    binding = {key: candidate[key] for key in ("repository", "sourceSha", "runId", "runAttempt")}
    binding.update(
        candidateArtifactId=candidate["artifactId"], candidateSha256=candidate["archiveSha256"]
    )
    require(report.get("candidate") == binding, "Additional lane source/bytes differ")
    require(
        report.get("schemaVersion") == 1 and report.get("status") == "passed",
        "Additional lane did not pass",
    )
    expected = SAFARI_SMOKE_SCENARIOS if native else PWA_SCENARIOS
    rows = report.get("scenarios", [])
    require(
        len(rows) == len(expected)
        and {row.get("id") for row in rows} == expected
        and all(row.get("status") == "passed" for row in rows),
        "Additional lane scenario set is incomplete",
    )
    require(
        report.get("osNotificationObserved") is False,
        "Engine/native smoke cannot claim observed OS notification delivery",
    )
    prefix = "native-safari-smoke" if native else "pwa-engine"
    payloads = {f"{prefix}/{name}": raw, f"{prefix}/candidate.json": identity_raw}
    if native:
        payloads[f"{prefix}/native-smoke.png"] = native_smoke_evidence(folder, report, candidate)
    else:
        pwa_engine_evidence(report)
    return payloads


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    # prepare() validates these at its entry. Preserve the original checkout-
    # relative spelling for portable receipt fields, not canonical host paths.
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--diagnostics", type=Path, required=True)
    parser.add_argument("--evidence", type=workspace_path, required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--channel", choices=("nightly", "beta", "rc"), required=True)
    parser.add_argument("--output", type=workspace_path, required=True)
    parser.add_argument("--pwa-evidence", type=workspace_path, required=True)
    parser.add_argument("--safari-evidence", type=workspace_path)
    args = parser.parse_args()
    from prepare_browser_candidate import identity_from_env

    candidate = prepare(
        args.artifacts,
        args.prepared,
        args.diagnostics,
        identity_from_env(),
        int(os.environ["CI_BROWSER_CANDIDATE_ARTIFACT_ID"]),
    )
    prepared = workspace_path(args.prepared, kind="directory", must_exist=True)
    root = Path(__file__).resolve().parents[1]
    matrix = json.loads((root / ".github/browser-compatibility.json").read_bytes())
    payloads = collect(
        candidate,
        matrix,
        (root / "scripts/browser-scenarios.json").read_bytes(),
        args.evidence,
        args.prefix,
        args.channel,
    )
    for name in (
        "candidate-provenance.json",
        "packaged-e2e.json",
        "manifest.json",
        "release-inputs-container.json",
        "release-plan.json",
    ):
        payloads[f"package/{name}"] = bounded_file(prepared / name, 2_000_000)
    payloads.update(additional_evidence(args.pwa_evidence, candidate, False))
    if args.channel == "rc":
        require(args.safari_evidence is not None, "RC requires actual Safari smoke evidence")
        payloads.update(additional_evidence(args.safari_evidence, candidate, True))
    archive(args.output, payloads)


if __name__ == "__main__":
    main()
