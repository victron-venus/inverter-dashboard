#!/usr/bin/env python3
"""Create pending native observation forms; never create accepted evidence."""

import argparse
import json
from pathlib import Path

from native_acceptance import candidate_binding, digest, read_json, require


def initialize(candidate, policy, root, observer):
    require(observer.strip(), "An observer identity is required")
    require(not root.exists(), "Capture directory already exists; preserve its evidence")
    root.mkdir(parents=True)
    binding = candidate_binding(candidate)
    refs = []
    for target in policy["targets"]:
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
        (root / name).write_bytes(raw)
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
    (root / "native-review.pending.json").write_text(
        json.dumps(review, indent=2) + "\n", encoding="utf-8"
    )


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
    initialize(read_json(args.candidate), read_json(args.policy), args.output, args.observer)
    print("Pending forms created; no native scenario is accepted.")


if __name__ == "__main__":
    main()
