#!/usr/bin/env python3
"""Create pending native observation forms; never create accepted evidence."""

import argparse
import json
import re
from pathlib import Path

from native_acceptance import candidate_binding, digest, read_json, require
from qualification_paths import workspace_path


def pending_documents(candidate, policy, observer):
    require(isinstance(observer, str) and observer.strip(), "An observer identity is required")
    binding = candidate_binding(candidate)
    require(
        policy.get("schemaVersion") == 1 and policy.get("requiredFor") == ["stable"],
        "Unsupported native policy",
    )
    targets = policy.get("targets")
    require(isinstance(targets, list) and targets, "Native policy targets are required")
    refs, documents = [], {}
    for target in targets:
        require(isinstance(target, dict), "Invalid native target")
        target_id = target.get("id")
        require(
            isinstance(target_id, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,63}", target_id),
            "Invalid native target id",
        )
        require(target_id + ".json" not in documents, "Duplicate native target")
        scenarios = target.get("scenarios")
        require(
            isinstance(scenarios, list)
            and scenarios
            and all(isinstance(item, str) and item for item in scenarios)
            and len(scenarios) == len(set(scenarios)),
            "Invalid native scenarios",
        )
        for key in ("platform", "browser", "installation"):
            require(isinstance(target.get(key), str) and target[key], "Missing native identity")
        receipt = {
            "schemaVersion": 1,
            "scope": "native-complete",
            "targetId": target["id"],
            "candidate": binding,
            "observer": observer,
            "startedAt": "",
            "finishedAt": "",
            "identity": {
                "platform": target["platform"],
                "browser": target["browser"],
                "installation": target["installation"],
                "runtime": "",
                "osVersion": "",
                "osBuild": "",
                "browserVersion": "",
                "userAgent": "",
                "deviceModel": "",
                "browserEngineEmulation": None,
                "observationMechanism": "",
            },
            "scenarios": [
                {
                    "id": scenario,
                    "status": "pending",
                    "observedAt": "",
                    "artifact": {"path": "", "sha256": ""},
                    "details": {},
                }
                for scenario in target["scenarios"]
            ],
        }
        name = target["id"] + ".json"
        raw = (json.dumps(receipt, indent=2) + "\n").encode()
        documents[name] = raw
        refs.append({"targetId": target["id"], "path": name, "sha256": digest(raw)})
    review = {
        "schemaVersion": 1,
        "status": "pending",
        "candidate": binding,
        "observer": observer,
        "reviewer": "",
        "reviewedAt": "",
        "receipts": refs,
    }
    documents["native-review.pending.json"] = (json.dumps(review, indent=2) + "\n").encode()
    return documents


def initialize(candidate, policy, root, observer):
    require(not root.exists(), "Capture directory already exists; preserve its evidence")
    # Validate and serialize every form before creating any filesystem state.
    documents = pending_documents(candidate, policy, observer)
    root.mkdir(parents=True)
    for name, raw in documents.items():
        destination = workspace_path(name, root=root, kind="file", new=True)
        with destination.open("xb") as handle:
            handle.write(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".github/native-acceptance-policy.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--observer", required=True)
    args = parser.parse_args()
    candidate = workspace_path(args.candidate, kind="file", must_exist=True)
    policy = workspace_path(args.policy, kind="file", must_exist=True)
    output = workspace_path(args.output, kind="directory", new=True)
    initialize(read_json(candidate), read_json(policy), output, args.observer)
    print("Pending forms created; no native scenario is accepted.")


if __name__ == "__main__":
    main()
