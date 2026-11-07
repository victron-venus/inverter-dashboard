"""Negative controls for the release boundary, not browser behavior mocks."""

import hashlib
import io
import json
import tarfile
import tempfile
from contextlib import redirect_stderr
from pathlib import Path
from unittest import TestCase, main, mock

import verify_browser_release_evidence as verifier

ROOT = Path(__file__).resolve().parents[1]


class BrowserReleaseEvidenceTests(TestCase):
    def test_archive_writer_rejects_outside_checkout_before_io(self):
        output = self.root / "outside.tar.gz"
        with self.assertRaisesRegex(ValueError, "escapes the trusted checkout"):
            verifier.archive(output, {"result.json": b"{}"})
        self.assertFalse(output.exists())

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
                "platform": {"ubuntu": "linux", "windows": "win32"}.get(
                    row["os"].split("-")[0], "darwin"
                ),
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
                ("target.json", dict(row, channel=row.get("channel", ""))),
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

    def test_target_default_channel_is_required_and_cannot_change(self):
        folder = self.reports("beta")
        self.mutate(folder, "target.json", lambda value: value.update(channel="chrome"))
        with self.assertRaisesRegex(ValueError, "target differs"):
            self.collect("beta")

    def test_missing_producer_normalized_channel_is_rejected(self):
        folder = self.reports("beta")
        self.mutate(folder, "target.json", lambda value: value.pop("channel"))
        with self.assertRaisesRegex(ValueError, "target differs"):
            self.collect("beta")

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
        verifier.archive(output, {"result.json": b"{}"}, root=self.root)
        before = output.read_bytes()
        with self.assertRaises(ValueError):
            verifier.archive(output, {"result.json": b"changed"}, root=self.root)
        self.assertEqual(before, output.read_bytes())

    def additional_report(self, native=False, folder=None):
        folder = folder or self.root
        (folder / "identity").mkdir(parents=True)
        (folder / "result").mkdir()
        self.candidate["frontendFiles"]["notifications-sw.js"] = "e" * 64
        (folder / "identity/candidate.json").write_text(json.dumps(self.candidate))
        binding = {
            key: self.candidate[key] for key in ("repository", "sourceSha", "runId", "runAttempt")
        }
        binding.update(candidateArtifactId=911, candidateSha256="b" * 64)
        names = verifier.SAFARI_SMOKE_SCENARIOS if native else verifier.PWA_SCENARIOS
        report = {
            "schemaVersion": 1,
            "status": "passed",
            "candidate": binding,
            "frontendFiles": dict(self.candidate["frontendFiles"]),
            "scenarios": [{"id": name, "status": "passed"} for name in sorted(names)],
            "osNotificationObserved": False,
        }
        if native:
            png = b"\x89PNG\r\n\x1a\nfixture"
            (folder / "result/native-smoke.png").write_bytes(png)
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
                sessionCleanupSucceeded=True,
                parentValidation={
                    "complete": True,
                    "applicationFrames": 0,
                    "protocolErrors": 0,
                    "httpWrites": 0,
                    "cleanupSucceeded": True,
                },
                networkIsolation={
                    "mechanism": "content-security-policy",
                    "scope": "candidate-document-resources",
                    "enforced": True,
                    "probeObserved": True,
                    "probeReports": 1,
                    "candidateViolations": 0,
                    "malformedReports": 0,
                    "policySha256": "f" * 64,
                },
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
        path = folder / "result" / ("native-smoke.json" if native else "result.json")
        path.write_text(json.dumps(report))
        return path

    def entry_fixture(self, channel):
        temporary = tempfile.TemporaryDirectory(prefix=".qualification-entry-", dir=ROOT)
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        self.root = directory / "reports"
        self.root.mkdir()
        self.candidate["frontendFiles"]["notifications-sw.js"] = "e" * 64
        self.reports(channel)
        pwa = directory / "pwa"
        self.additional_report(folder=pwa)
        safari = directory / "safari"
        if channel == "rc":
            self.additional_report(True, safari)
        prepared = directory / "prepared"
        prepared.mkdir()
        for name in (
            "candidate-provenance.json",
            "packaged-e2e.json",
            "manifest.json",
            "release-inputs-container.json",
            "release-plan.json",
        ):
            (prepared / name).write_bytes(b"{}")
        output = directory / "qualification.tar.gz"
        argv = ["verify_browser_release_evidence.py"]
        options = {
            "artifacts": directory / "artifacts",
            "prepared": prepared,
            "diagnostics": directory / "diagnostics",
            "evidence": self.root,
            "prefix": "browser-release",
            "channel": channel,
            "output": output,
            "pwa-evidence": pwa,
        }
        for key, value in options.items():
            argv.extend(["--" + key, str(value)])
        return argv, output, pwa, safari

    def run_entry(self, argv):
        # Candidate extraction has separate adversarial tests. Keep the real
        # matrix/PWA/Safari validators, CLI wiring and archive writer here.
        with (
            mock.patch("sys.argv", argv),
            mock.patch.object(verifier, "prepare", return_value=self.candidate),
            mock.patch.dict(
                "os.environ",
                {
                    "GITHUB_REPOSITORY": "fixture/dashboard",
                    "GITHUB_SHA": "a" * 40,
                    "GITHUB_RUN_ID": "71",
                    "GITHUB_RUN_ATTEMPT": "2",
                    "CI_BROWSER_CANDIDATE_ARTIFACT_ID": "911",
                },
            ),
        ):
            verifier.main()

    def test_entry_archives_pwa_and_rc_safari_with_original_screenshot(self):
        argv, output, pwa, safari = self.entry_fixture("rc")
        self.run_entry([*argv, "--safari-evidence", str(safari)])
        with tarfile.open(output) as bundle:
            pwa_member = bundle.extractfile("pwa-engine/result.json")
            screenshot = bundle.extractfile("native-safari-smoke/native-smoke.png")
            self.assertEqual(pwa_member.read(), (pwa / "result/result.json").read_bytes())
            self.assertEqual(screenshot.read(), (safari / "result/native-smoke.png").read_bytes())
            for prefix, folder in (("pwa-engine", pwa), ("native-safari-smoke", safari)):
                saved_identity = bundle.extractfile(f"{prefix}/candidate.json")
                self.assertEqual(
                    saved_identity.read(), (folder / "identity/candidate.json").read_bytes()
                )
            self.assertIn("package/release-plan.json", bundle.getnames())

    def test_additional_lanes_reject_missing_or_changed_measured_inventory(self):
        for native in (False, True):
            folder = self.root / ("safari" if native else "pwa")
            path = self.additional_report(native, folder)
            original = path.read_text()
            for inventory in (None, {}, {"index.html": "f" * 64}):
                with self.subTest(native=native, inventory=inventory):
                    path.write_text(original)
                    self.mutate(
                        path.parent,
                        path.name,
                        lambda value, expected=inventory: value.update(frontendFiles=expected),
                    )
                    with self.assertRaisesRegex(ValueError, "frontend inventory differs"):
                        verifier.additional_evidence(folder, self.candidate, native)

    def test_additional_lane_receipt_alone_cannot_replace_inventory(self):
        for native in (False, True):
            folder = self.root / ("safari" if native else "pwa")
            self.additional_report(native, folder)
            self.mutate(
                folder / "identity", "candidate.json", lambda value: value.update(frontendFiles={})
            )
            with (
                self.subTest(native=native),
                self.assertRaisesRegex(ValueError, "frontend inventory differs"),
            ):
                verifier.additional_evidence(folder, self.candidate, native)

    def test_matching_replaced_report_and_receipt_cannot_qualify_candidate(self):
        argv, output, pwa, safari = self.entry_fixture("rc")
        # Keep source/run/artifact identity and worker bytes, but substitute a
        # non-worker asset in both lane records. The independently prepared
        # release inventory must still reject them before an archive is written.
        for folder, name in ((pwa, "result.json"), (safari, "native-smoke.json")):
            identity = folder / "identity/candidate.json"
            report = folder / "result" / name
            originals = {path: path.read_bytes() for path in (identity, report)}
            for path in originals:
                self.mutate(
                    path.parent,
                    path.name,
                    lambda value: value["frontendFiles"].update({"index.html": "f" * 64}),
                )
            with (
                self.subTest(lane=name),
                self.assertRaisesRegex(ValueError, "frontend inventory differs"),
            ):
                self.run_entry([*argv, "--safari-evidence", str(safari)])
            self.assertFalse(output.exists())
            for path, raw in originals.items():
                path.write_bytes(raw)

    def test_entry_requires_pwa_evidence_before_writing_beta_archive(self):
        argv, output, pwa, _ = self.entry_fixture("beta")
        (pwa / "result/result.json").unlink()
        with self.assertRaises(ValueError):
            self.run_entry(argv)
        self.assertFalse(output.exists())

    def test_entry_requires_safari_evidence_before_writing_rc_archive(self):
        argv, output, _, _ = self.entry_fixture("rc")
        with self.assertRaisesRegex(ValueError, "RC requires actual Safari"):
            self.run_entry(argv)
        self.assertFalse(output.exists())

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

    def test_provisional_native_smoke_cannot_be_accepted(self):
        path = self.additional_report(True)
        self.mutate(
            path.parent,
            path.name,
            lambda value: value.update(status="pending-parent-validation"),
        )
        with self.assertRaisesRegex(ValueError, "did not pass"):
            verifier.additional_evidence(self.root, self.candidate, True)

    def test_native_session_cleanup_and_parent_checks_are_mandatory(self):
        path = self.additional_report(True)
        original = path.read_text()
        changes = [
            lambda value: value.pop("parentValidation"),
            lambda value: value.update(sessionCleanupSucceeded=False),
            lambda value: value.update(sessionCleanupFailed=True),
            lambda value: value["parentValidation"].update(cleanupSucceeded=False),
            lambda value: value["parentValidation"].update(applicationFrames=1),
            lambda value: value["parentValidation"].update(protocolErrors=1),
            lambda value: value["parentValidation"].update(httpWrites=1),
            lambda value: value["parentValidation"].update(httpWrites=False),
        ]
        for change in changes:
            path.write_text(original)
            self.mutate(path.parent, path.name, change)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "Native"):
                verifier.additional_evidence(self.root, self.candidate, True)

    def test_native_resource_isolation_cannot_be_unmeasured_or_overclaimed(self):
        path = self.additional_report(True)
        original = path.read_text()
        changes = [
            {"scope": "host-network"},
            {"enforced": False},
            {"probeReports": 0},
            {"candidateViolations": 1},
            {"malformedReports": 1},
            {"policySha256": "invalid"},
        ]
        for change in changes:
            path.write_text(original)
            self.mutate(
                path.parent,
                path.name,
                lambda value, update=change: value["networkIsolation"].update(update),
            )
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "Native"):
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
    main()
