#!/usr/bin/env python3
"""Adversarial candidate binding and safe extraction regression tests."""

import copy
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prepare_browser_candidate import (
    MQTT_SCENARIO,
    PACKAGED_SCENARIOS,
    digest,
    identity_from_env,
    manifest_inventory,
    prepare,
    read_bundle,
    safe_name,
    validate_bundle,
    version_plan,
)

PRODUCER = {
    "repository": "victron-venus/inverter-dashboard",
    "source_sha": "a" * 40,
    "run_id": 100,
    "run_attempt": 2,
}


def bundle_files(kind="python", channel="rc"):
    frontend = {
        "index.html": b"<html>candidate</html>",
        "assets/main.js": b"console.log(1)",
        "build-info.json": b'{"version":"1.0.0-rc.1"}',
    }
    inventory = {name: digest(data) for name, data in frontend.items()}
    manifest = {"repository": "https://github.com/victron-venus/inverter-dashboard-vue"}
    if kind == "python":
        manifest["files"] = [{"path": k, "sha256": v} for k, v in inventory.items()]
    else:
        manifest["sha256"] = inventory
    policy = json.loads((Path(__file__).resolve().parents[1] / ".release-policy.json").read_text())
    plan = version_plan.create_plan("1.0.0", channel, 1, PRODUCER["source_sha"], policy)
    inputs = [{"path": x["path"], "after_sha256": "e" * 64} for x in policy["versioning"]["files"]]
    receipt = {
        "plan_sha256": version_plan.plan_digest(plan),
        "files": inputs,
        "effective_inputs_sha256": version_plan.effective_inputs_digest(inputs),
        "producer": PRODUCER,
        "source_sha": PRODUCER["source_sha"],
        "artifacts": [{"name": "candidate.oci.tar", "size": 10, "sha256": "b" * 64}],
    }
    e2e = {
        "schema": 1,
        "containerArtifactId": 666,
        "planSha256": version_plan.plan_digest(plan),
        "networkInternal": True,
        "producer": PRODUCER,
        "ociSha256": "b" * 64,
        "runs": [
            {
                "transport": mode,
                "engine": engine,
                "status": "passed",
                "producer": PRODUCER,
                "ociSha256": "b" * 64,
                "backendVersion": version_plan.projections(plan)["package"],
                "browserVersion": "100.1",
                "scenarios": sorted(
                    PACKAGED_SCENARIOS | ({MQTT_SCENARIO} if mode == "mqtt" else set())
                ),
                "testsPassed": len(PACKAGED_SCENARIOS) + (mode == "mqtt"),
                "testsFailed": 0,
            }
            for mode in ("mqtt", "igw")
            for engine in ("chromium", "firefox", "webkit")
        ],
    }
    files = {"frontend/" + name: data for name, data in frontend.items()}
    for name, value in (
        ("release-plan.json", plan),
        ("manifest.json", manifest),
        ("release-inputs-container.json", receipt),
        ("packaged-e2e.json", e2e),
    ):
        files[name] = json.dumps(value).encode()
    metadata = {
        "schema": 1,
        "containerArtifactId": 666,
        "planSha256": version_plan.plan_digest(plan),
        "producer": PRODUCER,
        "ociArchive": "candidate.oci.tar",
        "ociSha256": "b" * 64,
        "ociSize": 10,
        "receiptSha256": digest(files["release-inputs-container.json"]),
        "manifestKind": kind,
        "manifestSha256": digest(files["manifest.json"]),
        "frontendFiles": inventory,
        "servedFiles": {
            k: v for k, v in inventory.items() if kind != "go" or k != "build-info.json"
        },
        "sourceOnlyMetadata": ["build-info.json"] if kind == "go" else [],
        "e2eSha256": digest(files["packaged-e2e.json"]),
    }
    files["candidate-provenance.json"] = json.dumps(metadata).encode()
    return files


def change(files, name, mutate):
    result = dict(files)
    value = json.loads(files[name])
    mutate(value)
    result[name] = json.dumps(value).encode()
    return result


def write_bundle(directory, files):
    archive = directory / "browser-candidate.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for name, data in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            bundle.addfile(entry, io.BytesIO(data))
    (directory / "browser-candidate.sha256").write_text(
        digest(archive.read_bytes()) + "  browser-candidate.tar.gz\n"
    )
    return archive


class CandidateTests(unittest.TestCase):
    def test_valid_python_and_go(self):
        for kind in ("python", "go"):
            metadata, files = validate_bundle(bundle_files(kind), PRODUCER)
            self.assertEqual(metadata["manifestKind"], kind)
            self.assertEqual(len(files), 3)

    def test_promote_bytes_rc_uses_base_and_beta_uses_channel_version(self):
        for channel in ("beta", "rc"):
            files = bundle_files(channel=channel)
            plan = json.loads(files["release-plan.json"])
            self.assertEqual(plan["version"], "1.0.0-" + channel + ".1")
            self.assertEqual(
                json.loads(files["packaged-e2e.json"])["runs"][0]["backendVersion"],
                "1.0.0" if channel == "rc" else "1.0.0-beta.1",
            )
            validate_bundle(files, PRODUCER)

    def test_duplicate_frozen_input_rejected_even_with_existing_digest(self):
        files = change(
            bundle_files(),
            "release-inputs-container.json",
            lambda x: x["files"].append(x["files"][0]),
        )
        with self.assertRaises(ValueError):
            validate_bundle(files, PRODUCER)

    def test_each_producer_dimension_is_required(self):
        for name in PRODUCER:
            bad = copy.deepcopy(PRODUCER)
            bad[name] = "wrong" if isinstance(bad[name], str) else bad[name] + 1
            files = bundle_files()
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_bundle(files, bad)

    def test_payload_corruption_and_unknown_file(self):
        for name in ("frontend/assets/main.js", "unattested.js"):
            files = bundle_files()
            files[name] = b"modified"
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_bundle(files, PRODUCER)

    def test_receipt_binding_rejects_each_mismatch(self):
        mutations = [
            lambda x: x.pop("producer"),
            lambda x: x.update(source_sha="c" * 40),
            lambda x: x["artifacts"][0].update(size=11),
            lambda x: x["artifacts"][0].update(sha256="c" * 64),
        ]
        for mutate in mutations:
            files = change(bundle_files(), "release-inputs-container.json", mutate)
            with self.assertRaises(ValueError):
                validate_bundle(files, PRODUCER)
            # Keep the outer checksum coherent so the producer/OCI binding
            # itself must reject, even if the receipt was deliberately rehashed.
            files = change(
                files,
                "candidate-provenance.json",
                lambda x, receipt=files["release-inputs-container.json"]: x.update(
                    receiptSha256=digest(receipt)
                ),
            )
            with self.assertRaises(ValueError):
                validate_bundle(files, PRODUCER)

    def test_missing_duplicate_failed_or_wrong_engine_evidence(self):
        mutations = [
            lambda x: x["runs"].pop(),
            lambda x: x["runs"].append(x["runs"][0]),
            lambda x: x["runs"][0].update(status="failed"),
            lambda x: x["runs"][0].update(engine="not-webkit"),
        ]
        for mutate in mutations:
            files = change(bundle_files(), "packaged-e2e.json", mutate)
            # Update its hash to prove the semantic contract, not just checksum, rejects it.
            files = change(
                files,
                "candidate-provenance.json",
                lambda x, evidence=files["packaged-e2e.json"]: x.update(e2eSha256=digest(evidence)),
            )
            with self.assertRaises(ValueError):
                validate_bundle(files, PRODUCER)

    def test_missing_scenario_or_false_count_fails_even_with_matching_hash(self):
        mutations = [
            lambda x: x["runs"][0]["scenarios"].pop(),
            lambda x: x["runs"][0].update(testsFailed=1),
            lambda x: x["runs"][0].update(browserVersion=""),
            lambda x: x["runs"][0].update(ociSha256="c" * 64),
        ]
        for mutate in mutations:
            files = change(bundle_files(), "packaged-e2e.json", mutate)
            files = change(
                files,
                "candidate-provenance.json",
                lambda x, evidence=files["packaged-e2e.json"]: x.update(e2eSha256=digest(evidence)),
            )
            with self.assertRaises(ValueError):
                validate_bundle(files, PRODUCER)

    def test_frozen_plan_and_effective_inputs_are_required(self):
        for name, mutate in (
            ("release-plan.json", lambda x: x.update(source_sha="c" * 40)),
            ("release-inputs-container.json", lambda x: x.update(plan_sha256="c" * 64)),
            ("release-inputs-container.json", lambda x: x.update(effective_inputs_sha256="c" * 64)),
        ):
            files = change(bundle_files(), name, mutate)
            with self.assertRaises(ValueError):
                validate_bundle(files, PRODUCER)

    def test_original_container_artifact_lineage_is_exact(self):
        for other in (None, True, 0, 999):
            files = change(
                bundle_files(),
                "packaged-e2e.json",
                lambda x, value=other: x.update(containerArtifactId=value),
            )
            files = change(
                files,
                "candidate-provenance.json",
                lambda x, evidence=files["packaged-e2e.json"]: x.update(e2eSha256=digest(evidence)),
            )
            with self.assertRaises(ValueError):
                validate_bundle(files, PRODUCER)

    def test_cannot_claim_executable_source_only(self):
        files = change(
            bundle_files("go"),
            "candidate-provenance.json",
            lambda x: x["sourceOnlyMetadata"].append("assets/main.js"),
        )
        with self.assertRaises(ValueError):
            validate_bundle(files, PRODUCER)

    def test_unserved_executable_rejected(self):
        files = change(
            bundle_files(),
            "candidate-provenance.json",
            lambda x: x["servedFiles"].pop("assets/main.js"),
        )
        with self.assertRaises(ValueError):
            validate_bundle(files, PRODUCER)

    def test_path_traversal_and_nonportable_names(self):
        for name in ("../a", "/a", "x/../a", "x\\a", "C:a", "x//a", "a.", "a ", "a\x00b"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                safe_name(name)

    def test_symlink_duplicate_and_traversal_tar_members(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "bad.tar.gz"
            for kind in ("symlink", "duplicate", "traversal"):
                with tarfile.open(archive, "w:gz") as tar:
                    item = tarfile.TarInfo("../x" if kind == "traversal" else "x")
                    if kind == "symlink":
                        item.type, item.linkname = tarfile.SYMTYPE, "/outside-candidate"
                    tar.addfile(item)
                    if kind == "duplicate":
                        tar.addfile(item)
                with self.subTest(kind=kind), self.assertRaises(ValueError):
                    read_bundle(archive)

    def test_cli_paths_cannot_escape_checkout_or_follow_diagnostics_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            write_bundle(artifacts, bundle_files())
            external = root.parent / "outside-qualification"
            cases = [
                (external, root / "out", root / "diag"),
                (artifacts, external, root / "diag"),
                (artifacts, root / "out", external),
            ]
            for inputs, destination, diagnostics in cases:
                with self.subTest(path=external), self.assertRaises(ValueError):
                    prepare(inputs, destination, diagnostics, PRODUCER, 1, root=root)
            target = root / "existing"
            target.mkdir()
            (root / "linked-diag").symlink_to(target, target_is_directory=True)
            with self.assertRaises(ValueError):
                prepare(artifacts, root / "out", root / "linked-diag", PRODUCER, 1, root=root)
            (target / "candidate.json").write_text("preserve")
            with self.assertRaises(ValueError):
                prepare(artifacts, root / "out", target, PRODUCER, 1, root=root)
            self.assertEqual((target / "candidate.json").read_text(), "preserve")
            self.assertFalse((root / "out").exists())

    def test_prepare_does_not_overwrite_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            artifacts = root / "inputs"
            artifacts.mkdir()
            write_bundle(artifacts, bundle_files())
            destination = root / "extracted"
            result = prepare(artifacts, destination, root / "diagnostics", PRODUCER, 123, root=root)
            self.assertEqual(result["artifactId"], 123)
            self.assertEqual(result["distribution"], "extracted/frontend")
            self.assertEqual(result["manifestPath"], "extracted/manifest.json")
            recorded = (root / "diagnostics/candidate.json").read_text()
            self.assertNotIn(str(root), recorded)
            self.assertNotIn(str(root.resolve()), recorded)
            self.assertFalse(Path(result["distribution"]).is_absolute())
            self.assertEqual(
                result["candidateSha256"],
                digest((artifacts / "browser-candidate.tar.gz").read_bytes()),
            )
            self.assertEqual(
                (destination / "frontend/index.html").read_bytes(), b"<html>candidate</html>"
            )
            with self.assertRaises(ValueError):
                prepare(artifacts, destination, root / "diagnostics", PRODUCER, 123, root=root)

    def test_missing_artifact_id_and_checksum_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            artifacts = root / "inputs"
            artifacts.mkdir()
            write_bundle(artifacts, bundle_files())
            with self.assertRaises(ValueError):
                prepare(artifacts, root / "out", root / "diag", PRODUCER, 0, root=root)
            (artifacts / "browser-candidate.sha256").write_text("0" * 64)
            with self.assertRaises(ValueError):
                prepare(artifacts, root / "out", root / "diag", PRODUCER, 1, root=root)
            self.assertFalse((root / "out").exists())

    def test_symlink_input_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            artifacts = root / "inputs"
            artifacts.mkdir()
            archive = write_bundle(artifacts, bundle_files())
            archive.rename(artifacts / "real.tar.gz")
            archive.symlink_to(artifacts / "real.tar.gz")
            with self.assertRaises(ValueError):
                prepare(artifacts, root / "out", root / "diag", PRODUCER, 1, root=root)

    def test_actions_identity_must_be_explicit(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            identity_from_env()

    def test_manifest_case_alias_rejected(self):
        manifest = json.loads(bundle_files()["manifest.json"])
        manifest["files"].append({"path": "INDEX.HTML", "sha256": "d" * 64})
        with self.assertRaises(ValueError):
            manifest_inventory(manifest, "python")


if __name__ == "__main__":
    unittest.main()
