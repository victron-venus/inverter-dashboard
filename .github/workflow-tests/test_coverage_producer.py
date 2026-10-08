"""Require the reviewed coverage producer identity, not just a reused commit."""

import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "coverage_contracts", Path(__file__).resolve().parents[2] / "scripts/workflow_contracts.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Cannot load workflow contract checks")
contracts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contracts)


class CoverageProducerIdentityTests(unittest.TestCase):
    def setUp(self):
        self.ref = "a" * 40
        self.report = {"name": "runtime", "path": "coverage.xml", "format": "cobertura"}
        self.artifact = "coverage-runtime"

    def test_shared_language_producers_match_the_complete_identity(self):
        for format_name, workflow in (("cobertura", "python-ci.yml"), ("go", "go-ci.yml")):
            with self.subTest(format=format_name):
                report = {**self.report, "format": format_name}
                producer = {
                    "uses": f"victron-venus/venus-os-ci-toolkit/.github/workflows/{workflow}@{self.ref}",
                    "with": {"coverage-artifact-name": self.artifact},
                }
                contracts.validate_coverage_producer(producer, report, self.ref)

    def test_reusable_non_producers_cannot_reuse_an_approved_commit(self):
        references = (
            f"untrusted/toolkit/.github/workflows/python-ci.yml@{self.ref}",
            f"victron-venus/venus-os-ci-toolkit/.github/workflows/coverage-upload.yml@{self.ref}",
            f"victron-venus/venus-os-ci-toolkit/.github/workflows/go-ci.yml@{self.ref}",
            "victron-venus/venus-os-ci-toolkit/.github/workflows/python-ci.yml@" + "b" * 40,
            f"./.github/workflows/python-ci.yml@{self.ref}",
        )
        for reference in references:
            producer = {"uses": reference, "with": {"coverage-artifact-name": self.artifact}}
            with (
                self.subTest(reference=reference),
                self.assertRaisesRegex(ValueError, "producer pin"),
            ):
                contracts.validate_coverage_producer(producer, self.report, self.ref)

    def test_custom_export_requires_the_upload_action_and_immutable_pin(self):
        step = {
            "id": "export_coverage_runtime",
            "uses": "actions/upload-artifact@" + "b" * 40,
            "with": {"name": self.artifact + "-${{ github.run_attempt }}", "path": "coverage.xml"},
        }
        contracts.validate_coverage_producer({"steps": [step]}, self.report, self.ref)
        for reference in (
            None,
            "actions/download-artifact@" + "b" * 40,
            "untrusted/upload-artifact@" + "b" * 40,
            "actions/upload-artifact@v4",
            "actions/upload-artifact@" + "b" * 39,
            "actions/upload-artifact@" + "b" * 40 + "suffix",
        ):
            changed = (
                {**step, "uses": reference}
                if reference is not None
                else {key: value for key, value in step.items() if key != "uses"}
            )
            with (
                self.subTest(reference=reference),
                self.assertRaisesRegex(ValueError, "producer export"),
            ):
                contracts.validate_coverage_producer({"steps": [changed]}, self.report, self.ref)

    def test_lcov_has_no_shared_language_producer(self):
        producer = {
            "uses": f"victron-venus/venus-os-ci-toolkit/.github/workflows/python-ci.yml@{self.ref}",
            "with": {"coverage-artifact-name": self.artifact},
        }
        report = {**self.report, "format": "lcov"}
        with self.assertRaisesRegex(ValueError, "producer pin"):
            contracts.validate_coverage_producer(producer, report, self.ref)
