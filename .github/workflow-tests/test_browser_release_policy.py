"""Keep RC qualification exhaustive without charging every beta for the full matrix."""

import json
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text())


class BrowserReleasePolicyTests(unittest.TestCase):
    def test_full_matrix_and_required_baseline_remain_complete(self):
        matrix = json.loads((ROOT / ".github/browser-compatibility.json").read_text())
        self.assertEqual(len(matrix), 35)
        self.assertEqual(len({row["id"] for row in matrix}), 35)
        self.assertEqual(sum(row["tier"] == "required" for row in matrix), 5)
        required = {(row["os"], row["engine"]) for row in matrix if row["tier"] == "required"}
        self.assertEqual(
            required,
            {
                ("ubuntu-latest", "chromium"),
                ("ubuntu-latest", "firefox"),
                ("ubuntu-latest", "webkit"),
                ("windows-2022", "chromium"),
                ("macos-15", "webkit"),
            },
        )

    def test_browser_route_remains_required_and_channel_selection_is_explicit(self):
        browser = workflow("ci.yml")["jobs"]["browser-compatibility"]
        self.assertEqual(
            browser["with"]["browser-extended"],
            "${{ github.event_name == 'schedule' || (github.event_name == 'workflow_dispatch' && (github.event.inputs.channel == '' || github.event.inputs.channel == 'rc')) }}",
        )
        self.assertEqual(
            browser["with"]["browser-release-channel"],
            "${{ github.event_name == 'workflow_dispatch' && github.event.inputs.channel || '' }}",
        )
        self.assertFalse(browser.get("continue-on-error", False))
        quality = workflow("quality-gate.yml")["jobs"]
        self.assertEqual(quality["check-0"]["uses"], "./.github/workflows/ci.yml")
        self.assertIn("check-0", quality["gate"]["needs"])


if __name__ == "__main__":
    unittest.main()
