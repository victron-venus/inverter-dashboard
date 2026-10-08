"""Preserve one filesystem-scan baseline through reusable workflow callers."""

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ".github/workflows/release-pipeline.yml:trivy-scan"


def definition(name):
    """Load scalar workflow values without constructing executable YAML objects."""
    # BaseLoader preserves the `on` key and only creates strings, lists and maps.
    return yaml.load(  # nosec B506
        (ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader
    )


class TrivyCategoryTests(unittest.TestCase):
    """Keep PR and release scans comparable without weakening scanner failures."""

    def test_upload_category_preserves_existing_main_baseline(self):
        workflow = definition("trivy-fs.yml")
        job = workflow["jobs"]["trivy-scan"]
        uploads = [
            step
            for step in job["steps"]
            if step.get("uses", "").startswith("github/codeql-action/upload-sarif@")
        ]
        self.assertEqual(len(uploads), 1)
        upload = uploads[0]
        self.assertEqual(upload["with"]["category"], BASELINE)
        self.assertEqual(upload["with"]["sarif_file"], "trivy-results.sarif")
        self.assertEqual(upload["if"], "always()")
        self.assertEqual(job["permissions"]["security-events"], "write")

    def test_all_current_call_paths_reach_the_same_failing_scan(self):
        workflow = definition("trivy-fs.yml")
        self.assertIn("workflow_call", workflow["on"])
        quality = definition("quality-gate.yml")
        self.assertIn("pull_request", quality["on"])
        self.assertEqual(quality["jobs"]["check-3"]["uses"], "./.github/workflows/trivy-fs.yml")
        release = definition("release-pipeline.yml")
        self.assertEqual(release["jobs"]["checks"]["uses"], "./.github/workflows/quality-gate.yml")
        scans = [
            step
            for step in workflow["jobs"]["trivy-scan"]["steps"]
            if step.get("uses", "").startswith("aquasecurity/trivy-action@")
        ]
        self.assertEqual(len(scans), 1)
        self.assertEqual(scans[0]["with"]["exit-code"], "1")
        self.assertEqual(scans[0]["with"]["severity"], "HIGH,CRITICAL")
        self.assertEqual(scans[0]["with"]["scan-ref"], ".")
        self.assertEqual(scans[0]["with"]["scan-type"], "fs")
        self.assertNotEqual(scans[0].get("continue-on-error"), "true")


if __name__ == "__main__":
    unittest.main()
