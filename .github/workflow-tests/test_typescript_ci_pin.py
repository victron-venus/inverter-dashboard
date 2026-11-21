"""Keep browser qualification callers on the qualified Toolkit revision."""

import json
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
POLICY = json.loads((ROOT / ".release-policy.json").read_text())
TOOLKIT_SHA = POLICY["versioning"]["toolkit_revision"]
REUSABLE = "victron-venus/venus-os-ci-toolkit/.github/workflows/typescript-ci.yml@" + TOOLKIT_SHA


class TypeScriptWorkflowPinTests(unittest.TestCase):
    """Release and ordinary CI must share the same reviewed browser engine."""

    def test_ci_browser_matrix_uses_versioned_toolkit_revision(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
        self.assertEqual(workflow["jobs"]["browser-compatibility"]["uses"], REUSABLE)

    def test_release_browser_matrix_uses_versioned_toolkit_revision(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/release-build.yml").read_text())
        self.assertEqual(workflow["jobs"]["browser-release"]["uses"], REUSABLE)

    # The Toolkit defaults this input to false on purpose: enabling it turns the
    # immutable upload step into continue-on-error and moves the gate to an exact
    # artifact read-back, so both callers must opt in deliberately rather than
    # inherit the recovery path from a workflow default.
    def test_ci_browser_matrix_opts_into_evidence_recovery(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
        with_ = workflow["jobs"]["browser-compatibility"]["with"]
        self.assertIs(with_["browser-evidence-upload-retry"], True)

    def test_release_browser_matrix_opts_into_evidence_recovery(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/release-build.yml").read_text())
        with_ = workflow["jobs"]["browser-release"]["with"]
        self.assertIs(with_["browser-evidence-upload-retry"], True)


if __name__ == "__main__":
    unittest.main()
