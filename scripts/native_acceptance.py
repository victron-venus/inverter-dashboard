#!/usr/bin/env python3
"""Validate reviewed native evidence against one immutable release candidate.

This checks evidence integrity and completeness, not the truth of an arbitrary
uploaded screenshot. The protected release workflow must authenticate the
independent review and its artifact before invoking this verifier.
"""

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = 1
CANDIDATE_FIELDS = (
    "repository",
    "sourceSha",
    "runId",
    "runAttempt",
    "candidateArtifactId",
    "candidateSha256",
)
UI_OBSERVERS = {"cua-repl", "human", "hosted-device-ui"}
SHA256 = re.compile(r"[a-f0-9]{64}")
SHA1 = re.compile(r"[a-f0-9]{40}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    data = path.read_bytes()
    require(len(data) <= 2_000_000, "JSON evidence exceeds the size limit")
    return json.loads(data)


def timestamp(value):
    require(isinstance(value, str), "Missing observation time")
    parsed = datetime.fromisoformat(value)
    require(parsed.utcoffset() is not None, "Observation time must include timezone")
    require(parsed <= datetime.now(UTC), "Future observation time")
    return parsed


def candidate_binding(candidate):
    binding = {key: candidate.get(key) for key in CANDIDATE_FIELDS}
    require(
        isinstance(binding["repository"], str) and REPOSITORY.fullmatch(binding["repository"]),
        "Invalid repository",
    )
    require(
        isinstance(binding["sourceSha"], str) and SHA1.fullmatch(binding["sourceSha"]),
        "Invalid source SHA",
    )
    require(
        isinstance(binding["candidateSha256"], str)
        and SHA256.fullmatch(binding["candidateSha256"]),
        "Invalid candidate digest",
    )
    for key in ("runId", "runAttempt", "candidateArtifactId"):
        require(type(binding[key]) is int and binding[key] > 0, f"Invalid {key}")
    for alias, key in (("artifactId", "candidateArtifactId"), ("archiveSha256", "candidateSha256")):
        if alias in candidate:
            require(candidate[alias] == binding[key], f"Conflicting candidate alias: {alias}")
    return binding


def evidence_file(root, reference):
    require(isinstance(reference, dict), "Missing artifact reference")
    name = reference.get("path")
    require(isinstance(name, str) and name and "\\" not in name, "Invalid evidence path")
    relative = Path(name)
    require(
        not relative.is_absolute() and all(part not in (".", "..") for part in name.split("/")),
        "Evidence path must be confined",
    )
    require(
        isinstance(reference.get("sha256"), str) and SHA256.fullmatch(reference["sha256"]),
        "Missing evidence digest",
    )
    path = root / relative
    for parent in [path, *path.parents]:
        require(not parent.is_symlink(), "Linked evidence is forbidden")
        if parent == root:
            break
    require(
        path.is_file() and path.resolve().is_relative_to(root.resolve()),
        "Evidence file is missing or escapes root",
    )
    require(0 < path.stat().st_size <= 50_000_000, "Evidence file is empty or too large")
    require(digest(path.read_bytes()) == reference["sha256"], "Evidence digest mismatch")
    return path


def nonempty(value):
    return isinstance(value, str) and 0 < len(value.strip()) <= 1000


def validate_identity(identity, target):
    require(isinstance(identity, dict), "Missing native identity")
    for key in ("platform", "browser", "installation"):
        require(identity.get(key) == target[key], f"Wrong native identity: {key}")
    require(
        identity.get("runtime") in target["runtimes"],
        "Emulation or unqualified runtime cannot count as native",
    )
    for key in ("osVersion", "osBuild", "browserVersion", "userAgent", "deviceModel"):
        require(nonempty(identity.get(key)), f"Missing native {key}")
    require(
        identity.get("browserEngineEmulation") is False,
        "Native identity must exclude browser engine emulation",
    )
    for key in ("osVersion", "browserVersion"):
        require(
            re.fullmatch(r"[0-9]+(?:[.][0-9]+)*(?:[A-Za-z0-9 ._-]*)", identity[key]),
            f"Invalid measured {key}",
        )
    # OS/UI observations cannot be supplied by safaridriver's glass-pane session.
    require(identity.get("observationMechanism") in UI_OBSERVERS, "Native UI observer is required")


def expected_details(detail, values, message):
    for key, expected in values.items():
        actual = detail.get(key)
        require(type(actual) is type(expected) and actual == expected, message + ": " + key)


def validate_worker_update(detail, candidate):
    expected = candidate.get("frontendFiles", {}).get("notifications-sw.js")
    require(
        expected and detail.get("candidateWorkerSha256") == expected,
        "Updated worker must match candidate bytes",
    )
    prior = detail.get("previousWorkerSha256")
    require(
        isinstance(prior, str) and SHA256.fullmatch(prior) and prior != expected,
        "A distinct preceding worker is required for upgrade acceptance",
    )
    expected_details(
        detail,
        {"settingsPreserved": True, "cacheStorageEntries": 0, "workerScope": "/"},
        "Worker upgrade/scope/no-cache observation missing",
    )


def validate_os_delivery(detail, identity):
    expected_details(
        detail,
        {"tabState": "closed", "queueAccepted": True, "providerAccepted": True},
        "Queue/provider/closed-tab evidence missing",
    )
    require(
        detail.get("osNotificationObserved") is True,
        "Provider acceptance is not observed OS display",
    )
    require(
        detail.get("observer") == identity["observationMechanism"],
        "OS observation mechanism mismatch",
    )
    require(
        isinstance(detail.get("eventKey"), str) and SHA256.fullmatch(detail["eventKey"]),
        "Missing test event identity",
    )
    source = detail.get("sourceTimestampMs")
    require(
        type(source) is int and source > 0 and source == detail.get("displayedSourceTimestampMs"),
        "Displayed notification must preserve event occurrence time",
    )


SIMPLE_DETAILS = {
    "native-runtime-identity": {"runtimeInventoryObserved": True, "browserIdentityObserved": True},
    "telemetry-alarms-controls": {
        "telemetryUpdated": True,
        "alarmSourceTimePreserved": True,
        "externalModesPresent": True,
        "rejectedCommandNotApplied": True,
        "fixtureOnly": True,
    },
    "keyboard-layout": {"settingsEditable": True, "controlsVisible": True},
    "touch-software-keyboard": {
        "settingsEditable": True,
        "controlsVisible": True,
        "touchObserved": True,
        "softwareKeyboardObserved": True,
    },
    "installed-launch": {"displayMode": "standalone", "launchedFromIcon": True},
    "background-resume-source-fence": {
        "backgrounded": True,
        "freshTelemetryAfterResume": True,
        "retiredEventRejected": True,
    },
    "permission-denied": {
        "permission": "denied",
        "registrationCreated": False,
        "permissionSource": "system-ui",
    },
    "permission-granted-subscription": {
        "permission": "granted",
        "registrationConfirmed": True,
        "permissionSource": "system-ui",
        "userGesture": True,
    },
    "subscription-revoke": {
        "serverRegistered": False,
        "browserSubscribed": False,
        "localEnabled": False,
    },
    "notification-click-root": {"openedPath": "/", "commandsEmitted": 0},
}


def validate_details(scenario, candidate, identity):
    name = scenario["id"]
    detail = scenario.get("details")
    require(isinstance(detail, dict), f"Missing measured details: {name}")
    if name in SIMPLE_DETAILS:
        expected_details(detail, SIMPLE_DETAILS[name], "Incomplete native observation " + name)
    elif name == "worker-update-no-cache":
        validate_worker_update(detail, candidate)
    elif name == "closed-tab-os-notification":
        validate_os_delivery(detail, identity)
    else:
        raise ValueError(f"Unknown native scenario: {name}")


def validate_receipt(receipt, reference, target, candidate, review, root):
    require(
        receipt.get("schemaVersion") == SCHEMA and receipt.get("scope") == "native-complete",
        "Partial smoke/engine evidence is not native acceptance",
    )
    require(receipt.get("targetId") == target["id"], "Receipt target mismatch")
    require(receipt.get("observer") == review["observer"], "Receipt observer mismatch")
    require(
        candidate_binding(receipt.get("candidate", {})) == candidate_binding(candidate),
        "Receipt candidate mismatch",
    )
    started, finished = timestamp(receipt.get("startedAt")), timestamp(receipt.get("finishedAt"))
    require(
        started <= finished <= timestamp(review.get("reviewedAt")),
        "Invalid observation/review ordering",
    )
    identity = receipt.get("identity", {})
    validate_identity(identity, target)
    scenarios = receipt.get("scenarios", [])
    required = target["scenarios"]
    require(
        len(required) == len(set(required))
        and len(scenarios) == len(required)
        and {item.get("id") for item in scenarios} == set(required),
        "Exact native scenario set is required",
    )
    for scenario in scenarios:
        require(
            scenario.get("status") == "passed",
            "Skipped, unsupported or failed native scenarios cannot qualify stable",
        )
        require(
            started <= timestamp(scenario.get("observedAt")) <= finished,
            "Scenario outside observation window",
        )
        evidence_file(root, scenario.get("artifact"))
        validate_details(scenario, candidate, identity)
    by_id = {item["id"]: item["details"] for item in scenarios}
    require(
        by_id["notification-click-root"].get("eventKey")
        == by_id["closed-tab-os-notification"]["eventKey"],
        "Click/delivery event mismatch",
    )
    return {
        "targetId": target["id"],
        "identity": identity,
        "scenarioCount": len(scenarios),
        "receiptSha256": reference["sha256"],
    }


def verify(candidate, policy, review, root):
    binding = candidate_binding(candidate)
    require(
        policy.get("schemaVersion") == SCHEMA and policy.get("requiredFor") == ["stable"],
        "Unsupported native policy",
    )
    require(
        review.get("schemaVersion") == SCHEMA and review.get("status") == "accepted",
        "Native review is not accepted",
    )
    require(candidate_binding(review.get("candidate", {})) == binding, "Review candidate mismatch")
    require(
        nonempty(review.get("reviewer"))
        and nonempty(review.get("observer"))
        and review["reviewer"] != review["observer"],
        "Independent review is required",
    )
    timestamp(review.get("reviewedAt"))
    targets = policy.get("targets", [])
    require(
        targets and len({target["id"] for target in targets}) == len(targets),
        "Policy targets must be unique",
    )
    receipts = review.get("receipts", [])
    require(
        len(receipts) == len(targets)
        and {item.get("targetId") for item in receipts} == {target["id"] for target in targets},
        "Exact native target set is required",
    )
    result = []
    for target in targets:
        ref = next(item for item in receipts if item["targetId"] == target["id"])
        receipt = read_json(evidence_file(root, ref))
        result.append(validate_receipt(receipt, ref, target, candidate, review, root))
    return {
        "schemaVersion": SCHEMA,
        "status": "accepted",
        "scope": policy["qualification"],
        "candidate": binding,
        "targets": result,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["verify"])
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".github/native-acceptance-policy.json",
    )
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # Never leave a stale positive verdict when this attempt fails.
    args.output.unlink(missing_ok=True)
    try:
        result = verify(
            read_json(args.candidate),
            read_json(args.policy),
            read_json(args.review),
            args.evidence_root,
        )
    except (ValueError, OSError, KeyError, TypeError, StopIteration) as exc:
        parser.exit(1, f"Native acceptance rejected: {exc}\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
