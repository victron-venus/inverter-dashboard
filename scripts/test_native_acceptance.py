"""Fail-closed native gate contracts; these are synthetic verifier fixtures."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import native_acceptance as gate


class NativeAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.policy = gate.read_json(
            Path(__file__).resolve().parents[1] / ".github/native-acceptance-policy.json"
        )
        self.candidate = {
            "repository": "victron-venus/inverter-dashboard",
            "sourceSha": "a" * 40,
            "runId": 10,
            "runAttempt": 2,
            "candidateArtifactId": 20,
            "candidateSha256": "b" * 64,
            "frontendFiles": {"notifications-sw.js": "c" * 64},
        }
        self.review = {
            "schemaVersion": 1,
            "status": "accepted",
            "candidate": gate.candidate_binding(self.candidate),
            "reviewer": "reviewer",
            "observer": "observer",
            "reviewedAt": "2020-01-01T00:03:00Z",
            "receipts": [],
        }
        artifact = self.write("observation.txt", b"SYNTHETIC UNIT TEST ONLY - not release evidence")
        self.receipts = {}
        for target in self.policy["targets"]:
            receipt = {
                "schemaVersion": 1,
                "scope": "native-complete",
                "candidate": gate.candidate_binding(self.candidate),
                "targetId": target["id"],
                "observer": "observer",
                "startedAt": "2020-01-01T00:00:00Z",
                "finishedAt": "2020-01-01T00:02:00Z",
                "identity": {
                    "platform": target["platform"],
                    "browser": target["browser"],
                    "installation": target["installation"],
                    "runtime": target["runtimes"][0],
                    "osVersion": "99.1.0",
                    "osBuild": "TEST",
                    "browserVersion": "99.0.0",
                    "userAgent": "TEST",
                    "deviceModel": "TEST",
                    "browserEngineEmulation": False,
                    "observationMechanism": "human",
                },
                "scenarios": [
                    {
                        "id": name,
                        "status": "passed",
                        "observedAt": "2020-01-01T00:01:00Z",
                        "artifact": artifact,
                        "details": self.details(name),
                    }
                    for name in target["scenarios"]
                ],
            }
            self.receipts[target["id"]] = receipt
        self.save_receipts()

    def write(self, name, data):
        (self.root / name).write_bytes(data)
        return {"path": name, "sha256": gate.digest(data)}

    def save_receipts(self):
        self.review["receipts"] = []
        for target, receipt in self.receipts.items():
            ref = self.write(target + ".json", json.dumps(receipt).encode())
            self.review["receipts"].append({"targetId": target, **ref})

    def details(self, name):
        return {
            "native-runtime-identity": {
                "runtimeInventoryObserved": True,
                "browserIdentityObserved": True,
            },
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
            "worker-update-no-cache": {
                "candidateWorkerSha256": "c" * 64,
                "previousWorkerSha256": "d" * 64,
                "settingsPreserved": True,
                "cacheStorageEntries": 0,
                "workerScope": "/",
            },
            "closed-tab-os-notification": {
                "tabState": "closed",
                "queueAccepted": True,
                "providerAccepted": True,
                "osNotificationObserved": True,
                "observer": "human",
                "eventKey": "e" * 64,
                "sourceTimestampMs": 1577836860000,
                "displayedSourceTimestampMs": 1577836860000,
            },
            "notification-click-root": {
                "openedPath": "/",
                "commandsEmitted": 0,
                "eventKey": "e" * 64,
            },
        }[name]

    def verify(self):
        return gate.verify(self.candidate, self.policy, self.review, self.root)

    def first(self):
        return self.receipts["macos-safari"]

    def scenario(self, name):
        return next(item for item in self.first()["scenarios"] if item["id"] == name)

    def assert_rejected(self, fragment):
        self.save_receipts()
        with self.assertRaisesRegex(ValueError, fragment):
            self.verify()

    def test_complete_fixture_passes_but_is_only_synthetic_contract_test(self):
        result = self.verify()
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(len(result["targets"]), 3)

    def test_every_candidate_identity_field_is_bound(self):
        for key in gate.CANDIDATE_FIELDS:
            with self.subTest(key=key):
                old = self.first()["candidate"][key]
                self.first()["candidate"][key] = (
                    old + 1 if isinstance(old, int) else ("z" + old[1:])
                )
                self.save_receipts()
                with self.assertRaises(ValueError):
                    self.verify()
                self.first()["candidate"][key] = old

    def test_empty_missing_or_duplicate_target_fails(self):
        for refs in ([], self.review["receipts"][:-1], [self.review["receipts"][0]] * 3):
            original = self.review["receipts"]
            self.review["receipts"] = refs
            with self.assertRaisesRegex(ValueError, "target set"):
                self.verify()
            self.review["receipts"] = original

    def test_missing_extra_duplicate_or_skipped_scenarios_fail(self):
        original = copy.deepcopy(self.first()["scenarios"])
        variants = [original[:-1], original + [original[0]], [original[0]] * len(original)]
        for variant in variants:
            self.first()["scenarios"] = variant
            self.assert_rejected("scenario set")
        self.first()["scenarios"] = original
        for status in ("skipped", "unsupported", "failed"):
            self.first()["scenarios"][0]["status"] = status
            self.assert_rejected("Skipped")

    def test_provider_acceptance_does_not_prove_display(self):
        self.scenario("closed-tab-os-notification")["details"]["osNotificationObserved"] = False
        self.assert_rejected("not observed OS")

    def test_webdriver_does_not_prove_os_ui(self):
        self.first()["identity"]["observationMechanism"] = "safaridriver"
        self.assert_rejected("Native UI observer")

    def test_playwright_mobile_emulation_is_not_native(self):
        self.first()["identity"]["runtime"] = "playwright-emulation"
        self.assert_rejected("Emulation")

    def test_ios_simulator_is_accepted_when_complete_observations_exist(self):
        self.receipts["ios-safari-pwa"]["identity"]["runtime"] = "native-simulator"
        self.save_receipts()
        self.assertEqual(self.verify()["status"], "accepted")

    def test_engine_or_partial_smoke_receipt_fails(self):
        for scope in ("engine-pwa-isolated", "native-smoke"):
            self.first()["scope"] = scope
            self.assert_rejected("Partial smoke")

    def test_substitution_of_permission_override_fails(self):
        self.scenario("permission-denied")["details"]["permissionSource"] = "browser-test-override"
        self.assert_rejected("permissionSource")

    def test_update_must_be_to_exact_candidate(self):
        self.scenario("worker-update-no-cache")["details"]["candidateWorkerSha256"] = "f" * 64
        self.assert_rejected("match candidate")

    def test_unchanged_worker_is_not_upgrade_acceptance(self):
        self.scenario("worker-update-no-cache")["details"]["previousWorkerSha256"] = "c" * 64
        self.assert_rejected("distinct preceding")

    def test_click_and_display_must_be_same_event(self):
        self.scenario("notification-click-root")["details"]["eventKey"] = "f" * 64
        self.assert_rejected("Click/delivery")

    def test_received_time_cannot_replace_source_time(self):
        self.scenario("closed-tab-os-notification")["details"]["displayedSourceTimestampMs"] += 1000
        self.assert_rejected("occurrence time")

    def test_review_must_be_independent(self):
        self.review["reviewer"] = self.review["observer"]
        self.assert_rejected("Independent review")

    def test_artifact_tampering_fails(self):
        (self.root / "observation.txt").write_text("Changed")
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            self.verify()

    def test_symlink_and_traversal_artifacts_fail(self):
        reference = self.first()["scenarios"][0]["artifact"]
        (self.root / "linked.txt").symlink_to(self.root / "observation.txt")
        reference["path"] = "linked.txt"
        self.assert_rejected("Linked evidence")
        reference["path"] = "../observation.txt"
        self.assert_rejected("confined")

    def test_boolean_ids_are_invalid(self):
        for field in ("runId", "runAttempt", "candidateArtifactId"):
            candidate = {**self.candidate, field: True}
            with self.assertRaisesRegex(ValueError, "Invalid"):
                gate.candidate_binding(candidate)

    def test_conflicting_alias_fails(self):
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            gate.candidate_binding({**self.candidate, "artifactId": 99})

    def test_observation_after_review_fails(self):
        self.review["reviewedAt"] = "2020-01-01T00:00:01Z"
        self.assert_rejected("ordering")

    def test_measured_versions_use_bounded_ascii_validation(self):
        for version in ("18.5", "156.0.8078.4", "18.5 beta-2_123"):
            self.first()["identity"]["browserVersion"] = version
            self.save_receipts()
            self.assertEqual(self.verify()["status"], "accepted")
        for version in ("١.٢", "v18.5", "1." * 1000, "18.5/escape"):
            self.first()["identity"]["browserVersion"] = version
            self.save_receipts()
            with self.assertRaises(ValueError):
                self.verify()

    def test_cli_rejects_output_outside_checkout_before_deletion(self):
        output = self.root / "preserve.json"
        output.write_text("preserved")
        checkout = Path(__file__).resolve().parents[1]
        policy = checkout / ".github/native-acceptance-policy.json"
        arguments = [
            "native_acceptance.py",
            "verify",
            "--candidate",
            str(policy),
            "--review",
            str(policy),
            "--evidence-root",
            str(policy.parent),
            "--output",
            str(output),
        ]
        with patch("sys.argv", arguments), self.assertRaises(SystemExit):
            gate.main()
        self.assertEqual(output.read_text(), "preserved")

    def test_cli_cannot_overwrite_its_policy_input(self):
        checkout = Path(__file__).resolve().parents[1]
        policy = checkout / ".github/native-acceptance-policy.json"
        before = policy.read_bytes()
        arguments = [
            "native_acceptance.py",
            "verify",
            "--candidate",
            str(policy),
            "--review",
            str(policy),
            "--evidence-root",
            str(policy.parent),
            "--output",
            str(policy),
        ]
        with patch("sys.argv", arguments), self.assertRaises(SystemExit):
            gate.main()
        self.assertEqual(policy.read_bytes(), before)

    def test_cli_preserves_unrelated_existing_output_on_failed_verification(self):
        checkout = Path(__file__).resolve().parents[1]
        policy = checkout / ".github/native-acceptance-policy.json"
        with tempfile.TemporaryDirectory(prefix=".native-contract-", dir=checkout) as temporary:
            output = Path(temporary) / "keep.json"
            output.write_text("Existing independent evidence")
            arguments = [
                "native_acceptance.py",
                "verify",
                "--candidate",
                str(policy),
                "--review",
                str(policy),
                "--evidence-root",
                str(policy.parent),
                "--output",
                str(output),
            ]
            with patch("sys.argv", arguments), self.assertRaises(SystemExit):
                gate.main()
            self.assertEqual(output.read_text(), "Existing independent evidence")


if __name__ == "__main__":
    unittest.main()
