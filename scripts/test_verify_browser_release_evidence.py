"""Negative controls for the release boundary, not browser behavior mocks."""

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

import verify_browser_release_evidence as verifier

ROOT = Path(__file__).resolve().parents[1]


class BrowserReleaseEvidenceTests(unittest.TestCase):
    def test_cli_rejects_external_safari_path_before_candidate_io(self):
        argv = [
            "verify_browser_release_evidence.py",
            "--artifacts",
            "fixture/artifacts",
            "--prepared",
            "fixture/prepared",
            "--diagnostics",
            "fixture/diagnostics",
            "--evidence",
            "fixture/evidence",
            "--prefix",
            "browser-release",
            "--channel",
            "rc",
            "--output",
            "fixture/output.tar.gz",
            "--pwa-evidence",
            "fixture/pwa",
            "--safari-evidence",
            str(ROOT.parent / "outside-checkout"),
        ]
        error = io.StringIO()
        with (
            mock.patch("sys.argv", argv),
            mock.patch.object(verifier, "prepare") as prepare,
            redirect_stderr(error),
            self.assertRaises(SystemExit) as stopped,
        ):
            verifier.main()
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn("--safari-evidence", error.getvalue())
        prepare.assert_not_called()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.matrix = json.loads((ROOT / ".github/browser-compatibility.json").read_text())
        self.contract = (ROOT / "scripts/browser-scenarios.json").read_bytes()
        self.scenarios = json.loads(self.contract)
        self.candidate = {
            "repository": "fixture/dashboard",
            "sourceSha": "a" * 40,
            "runId": 71,
            "runAttempt": 2,
            "artifactId": 911,
            "containerArtifactId": 910,
            "archiveSha256": "b" * 64,
            "ociSha256": "c" * 64,
            "receiptSha256": "d" * 64,
            "planSha256": "f" * 64,
            "frontendFiles": {"index.html": "e" * 64},
        }

    def reports(self, channel="rc"):
        rows = (
            self.matrix if channel == "rc" else [r for r in self.matrix if r["tier"] == "required"]
        )
        for row in rows:
            folder = self.root / f"browser-release-{row['id']}-2"
            folder.mkdir()
            runtime = {
                "schema": 1,
                "sourceSha": "a" * 40,
                "runId": "71",
                "runAttempt": "2",
                "releaseChannel": channel,
                "candidateArtifactId": "911",
                "target": row["id"],
                "engine": row["engine"],
                "channel": row.get("channel", ""),
                "playwrightVersion": row["playwright"],
                "browserVersion": "123.1.2" if row.get("channel") else row["browser_version"],
                "platform": "linux"
                if row["os"].startswith("ubuntu-")
                else ("win32" if row["os"].startswith("windows-") else "darwin"),
            }
            names = self.scenarios["scenarios"]
            result = dict(
                runtime,
                testsPassed=len(names),
                testsFailed=0,
                scenarios=names,
                candidate=self.candidate,
                scenarioContract={
                    "id": self.scenarios["id"],
                    "sha256": hashlib.sha256(self.contract).hexdigest(),
                    "required": len(names),
                },
            )
            for filename, value in (
                ("runtime.json", runtime),
                ("target.json", row),
                ("result.json", result),
                ("candidate.json", self.candidate),
            ):
                (folder / filename).write_text(json.dumps(value))
        return self.root / f"browser-release-{rows[0]['id']}-2"

    def collect(self, channel="rc"):
        return verifier.collect(
            self.candidate, self.matrix, self.contract, self.root, "browser-release", channel
        )

    def mutate(self, folder, name, change):
        path = folder / name
        value = json.loads(path.read_text())
        change(value)
        path.write_text(json.dumps(value))

    def test_full_rc_archive_preserves_original_evidence_and_marks_native_unqualified(self):
        first = self.reports()
        payloads = self.collect()
        original = (first / "result.json").read_bytes()
        self.assertIn(original, payloads.values())
        verdict = json.loads(payloads["qualification.json"])
        self.assertEqual(verdict["targetCount"], 35)
        self.assertTrue(verdict["fullVersionMatrix"])
        self.assertFalse(verdict["nativeDeviceQualified"])
        self.assertFalse(verdict["osNotificationDisplayQualified"])

    def test_beta_uses_required_baseline_but_cannot_qualify_rc(self):
        self.reports("beta")
        self.assertEqual(json.loads(self.collect("beta")["qualification.json"])["targetCount"], 5)
        with self.assertRaisesRegex(ValueError, "Missing, reduced"):
            self.collect("rc")

    def test_historical_source_only_browser_result_is_rejected(self):
        folder = self.reports()
        self.mutate(folder, "result.json", lambda value: value.update(candidateArtifactId=""))
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            self.collect()

    def test_same_artifact_id_with_other_candidate_bytes_is_rejected(self):
        folder = self.reports()
        self.mutate(
            folder, "result.json", lambda value: value["candidate"].update(ociSha256="f" * 64)
        )
        with self.assertRaisesRegex(ValueError, "candidate bytes differ"):
            self.collect()

    def test_duplicate_scenario_cannot_replace_required_behavior(self):
        folder = self.reports()
        self.mutate(
            folder,
            "result.json",
            lambda value: value["scenarios"].__setitem__(0, value["scenarios"][1]),
        )
        with self.assertRaisesRegex(ValueError, "Missing, duplicate"):
            self.collect()

    def test_changed_contract_digest_is_rejected(self):
        folder = self.reports()
        self.mutate(
            folder, "result.json", lambda value: value["scenarioContract"].update(sha256="f" * 64)
        )
        with self.assertRaisesRegex(ValueError, "contract changed"):
            self.collect()

    def test_previous_attempt_runtime_cannot_qualify_current_report(self):
        folder = self.reports()
        self.mutate(folder, "runtime.json", lambda value: value.update(runAttempt="1"))
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            self.collect()

    def test_candidate_frontend_inventory_cannot_be_substituted(self):
        folder = self.reports()
        self.mutate(folder, "candidate.json", lambda value: value.update(frontendFiles={}))
        with self.assertRaisesRegex(ValueError, "frontend inventory differs"):
            self.collect()

    def test_linked_evidence_and_unknown_target_are_rejected(self):
        folder = self.reports()
        path = folder / "result.json"
        saved = folder / "foreign-result.json"
        path.rename(saved)
        path.symlink_to(saved)
        with self.assertRaises(ValueError):
            self.collect()

    def test_zero_failures_must_be_integer_not_boolean(self):
        folder = self.reports()
        self.mutate(folder, "result.json", lambda value: value.update(testsFailed=False))
        with self.assertRaisesRegex(ValueError, "Failed browser scenarios"):
            self.collect()

    def test_pinned_engine_version_cannot_drift(self):
        folder = self.reports()
        for filename in ("runtime.json", "result.json"):
            self.mutate(folder, filename, lambda value: value.update(browserVersion="999.0"))
        with self.assertRaisesRegex(ValueError, "Wrong pinned"):
            self.collect()

    def test_archive_does_not_replace_previous_evidence(self):
        output = self.root / "qualification.tar.gz"
        verifier.archive(output, {"result.json": b"{}"})
        before = output.read_bytes()
        with self.assertRaises(ValueError):
            verifier.archive(output, {"result.json": b"changed"})
        self.assertEqual(before, output.read_bytes())

    def additional_report(self, native=False):
        (self.root / "identity").mkdir()
        (self.root / "result").mkdir()
        self.candidate["frontendFiles"]["notifications-sw.js"] = "e" * 64
        (self.root / "identity/candidate.json").write_text(json.dumps(self.candidate))
        binding = {
            key: self.candidate[key] for key in ("repository", "sourceSha", "runId", "runAttempt")
        }
        binding.update(candidateArtifactId=911, candidateSha256="b" * 64)
        names = verifier.SAFARI_SMOKE_SCENARIOS if native else verifier.PWA_SCENARIOS
        report = {
            "schemaVersion": 1,
            "status": "passed",
            "candidate": binding,
            "scenarios": [{"id": name, "status": "passed"} for name in sorted(names)],
            "osNotificationObserved": False,
        }
        if native:
            png = b"\x89PNG\r\n\x1a\nfixture"
            (self.root / "result/native-smoke.png").write_bytes(png)
            report.update(
                driverCapabilities={
                    "browserName": "safari",
                    "platformName": "mac",
                    "browserVersion": "27.2",
                },
                runtimeIdentity={
                    "secureContext": True,
                    "serviceWorker": True,
                    "userAgent": "fixture Safari",
                },
                hostIdentity={
                    "osName": "macOS",
                    "osVersion": "15.7",
                    "osBuild": "24G100",
                    "architecture": "arm64",
                },
                scope="native-smoke",
                nativeComplete=False,
                requestedPlatform="macOS",
                runtime="native-host",
                workerSha256="e" * 64,
                screenshot={"path": "native-smoke.png", "sha256": verifier.sha256(png)},
            )
        else:
            report.update(
                browserVersion="156.0.8078.4",
                permissionMechanism="browser-test-override",
                subscriptionMechanism="isolated-ui-fixture",
                tlsTrust="isolated-self-signed",
                browserContextMode="isolated-persistent-profile",
                scope="engine-pwa-isolated",
                engine="chromium",
                serviceWorkers="allow",
                failures=[],
                nativeQualified=False,
                previousVersionUpgradeQualified=False,
            )
        path = self.root / "result" / ("native-smoke.json" if native else "result.json")
        path.write_text(json.dumps(report))
        return path

    def test_actual_worker_lane_is_preserved_as_engine_only(self):
        self.additional_report()
        result = verifier.additional_evidence(self.root, self.candidate, False)
        self.assertIn("pwa-engine/result.json", result)

    def test_engine_report_cannot_claim_os_banner_delivery(self):
        path = self.additional_report()
        self.mutate(path.parent, path.name, lambda value: value.update(osNotificationObserved=True))
        with self.assertRaisesRegex(ValueError, "cannot claim"):
            verifier.additional_evidence(self.root, self.candidate, False)

    def test_worker_lane_must_enable_service_workers(self):
        path = self.additional_report()
        self.mutate(path.parent, path.name, lambda value: value.update(serviceWorkers="block"))
        with self.assertRaisesRegex(ValueError, "required engine lane"):
            verifier.additional_evidence(self.root, self.candidate, False)

    def test_native_smoke_remains_partial_and_preserves_its_image(self):
        self.additional_report(True)
        result = verifier.additional_evidence(self.root, self.candidate, True)
        self.assertIn("native-safari-smoke/native-smoke.png", result)
        self.assertFalse(
            json.loads(result["native-safari-smoke/native-smoke.json"])["nativeComplete"]
        )

    def test_native_smoke_cannot_substitute_another_worker(self):
        path = self.additional_report(True)
        self.mutate(path.parent, path.name, lambda value: value.update(workerSha256="f" * 64))
        with self.assertRaisesRegex(ValueError, "another worker"):
            verifier.additional_evidence(self.root, self.candidate, True)

    def test_native_screenshot_bytes_are_verified(self):
        self.additional_report(True)
        (self.root / "result/native-smoke.png").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "screenshot is missing or changed"):
            verifier.additional_evidence(self.root, self.candidate, True)

    def test_safari_smoke_must_identify_branded_browser(self):
        path = self.additional_report(True)
        self.mutate(path.parent, path.name, lambda value: value.update(driverCapabilities={}))
        with self.assertRaisesRegex(ValueError, "measured Safari runtime"):
            verifier.additional_evidence(self.root, self.candidate, True)

    def test_pwa_fixture_must_identify_permission_override(self):
        path = self.additional_report()
        self.mutate(
            path.parent, path.name, lambda value: value.update(permissionMechanism="system-ui")
        )
        with self.assertRaisesRegex(ValueError, "synthetic boundaries"):
            verifier.additional_evidence(self.root, self.candidate, False)


if __name__ == "__main__":
    unittest.main()
