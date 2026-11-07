"""Keep exact-package browser evidence on every candidate publication path."""

import json
import unittest
from fnmatch import fnmatchcase
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def jobs(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text())["jobs"]


class PackagedBrowserQualificationTests(unittest.TestCase):
    def test_container_bytes_feed_runtime_then_same_candidate_matrix(self):
        workflow = jobs("release-build.yml")
        runtime = workflow["packaged-browser"]
        self.assertEqual(runtime["needs"], "container")
        download = next(
            step
            for step in runtime["steps"]
            if step.get("name") == "Download exact same-run container artifact"
        )
        self.assertEqual(
            download["with"]["artifact-ids"], "${{ needs.container.outputs.oci-artifact-id }}"
        )
        matrix = workflow["browser-release"]
        self.assertEqual(matrix["needs"], "packaged-browser")
        self.assertEqual(
            matrix["with"]["browser-artifact-id"],
            "${{ needs.packaged-browser.outputs.browser-artifact-id }}",
        )
        self.assertEqual(matrix["with"]["browser-extended"], "${{ inputs.channel == 'rc' }}")
        self.assertEqual(matrix["with"]["browser-evidence-prefix"], "browser-release")
        self.assertIn("prepare_browser_candidate.py", matrix["with"]["browser-prepare-command"])

    def test_preserved_release_evidence_depends_on_every_lane(self):
        workflow = jobs("release-build.yml")
        preserve = workflow["browser-qualification"]
        self.assertEqual(
            set(preserve["needs"]), {"packaged-browser", "browser-release", "native-browser"}
        )
        self.assertEqual(preserve["uses"], "./.github/workflows/browser-release-evidence.yml")
        for name in (
            "packaged-browser",
            "browser-release",
            "native-browser",
            "browser-qualification",
        ):
            self.assertNotIn("continue-on-error", workflow[name])
            self.assertNotIn("if", workflow[name])
        policy = json.loads((ROOT / ".release-policy.json").read_text())
        self.assertIn(
            "release-inputs-browser-qualification.json",
            policy["versioning"]["required_build_receipts"],
        )
        self.assertEqual(policy["versioning"]["promotion"], "promote-bytes")

    def test_pwa_is_mandatory_and_actual_safari_smoke_is_required_for_rc(self):
        native = jobs("native-browser-acceptance.yml")
        self.assertEqual(set(native), {"pwa", "safari"})
        self.assertNotIn("if", native["pwa"])
        self.assertEqual(native["safari"]["if"], "${{ inputs.channel == 'rc' }}")
        self.assertEqual(native["safari"]["runs-on"], "macos-15")
        for job in native.values():
            self.assertNotIn("continue-on-error", job)
            self.assertEqual(job["permissions"], {"contents": "read", "actions": "read"})
            steps = job["steps"]
            downloads = [step for step in steps if "artifact-ids" in step.get("with", {})]
            self.assertEqual(len(downloads), 1)
            self.assertEqual(
                downloads[0]["with"]["artifact-ids"], "${{ inputs.candidate_artifact_id }}"
            )
            self.assertTrue(downloads[0]["with"]["merge-multiple"])

    def test_native_helper_uses_the_measured_setup_python_output(self):
        steps = jobs("native-browser-acceptance.yml")["safari"]["steps"]
        python = next(step for step in steps if step.get("id") == "python")
        self.assertTrue(python["uses"].startswith("actions/setup-python@"))
        self.assertEqual(python["with"]["python-version"], "3.12")
        smoke = next(step for step in steps if "native_acceptance_smoke.cjs" in step.get("run", ""))
        self.assertEqual(
            smoke["env"],
            {
                "CI_NATIVE_PYTHON": "${{ steps.python.outputs.python-path }}",
                "CI_NATIVE_PYTHON_VERSION": "${{ steps.python.outputs.python-version }}",
            },
        )

    def test_evidence_payload_uses_original_plan_and_is_uploaded_for_publisher(self):
        steps = jobs("browser-release-evidence.yml")["preserve"]["steps"]
        commands = "\n".join(step.get("run", "") for step in steps)
        self.assertIn("verify_browser_release_evidence.py", commands)
        self.assertIn("--pwa-evidence", commands)
        self.assertIn("--safari-evidence", commands)
        self.assertIn("version_plan.py sync --plan .release-plan.json", commands)
        self.assertIn("stage_release_assets.py --target browser-qualification", commands)
        upload = steps[-1]["with"]
        self.assertEqual(
            upload["name"],
            "release-assets-browser-qualification-run-${{ github.run_id }}-attempt-${{ github.run_attempt }}",
        )
        pipeline = jobs("release-pipeline.yml")
        for publisher in (pipeline["candidate"], pipeline["final"]):
            pattern = next(
                step["with"]["pattern"]
                for step in publisher["steps"]
                if "pattern" in step.get("with", {})
            )
            self.assertTrue(fnmatchcase(upload["name"], pattern))
        self.assertEqual(upload["if-no-files-found"], "error")

    def test_reviewed_native_observation_is_not_misrepresented_as_engine_success(self):
        policy = json.loads((ROOT / ".github/native-acceptance-policy.json").read_text())
        self.assertEqual(policy["requiredFor"], ["stable"])
        self.assertEqual(
            {row["id"] for row in policy["targets"]},
            {"macos-safari", "ios-safari-pwa", "android-chrome-pwa"},
        )
        for target in policy["targets"]:
            self.assertIn("closed-tab-os-notification", target["scenarios"])
            self.assertIn("notification-click-root", target["scenarios"])
        release = jobs("release-pipeline.yml")
        self.assertEqual(release["build"]["uses"], "./.github/workflows/release-build.yml")
        # Full native evidence remains a protected maintainer review; this source
        # test must never relabel the automated partial smoke as that acceptance.
        self.assertIn("build", release["gate"]["needs"])


if __name__ == "__main__":
    unittest.main()
