# OpenSSF Best Practices evidence: inverter-dashboard

This index supports review against the [OpenSSF Passing criteria](https://www.bestpractices.dev/en/criteria/0). It is not an awarded badge, a security guarantee or a completed self-attestation. Initial source inventory: `ed5617f0806b9cd6182ddfc60ef35f2e51e7aace`. Re-check the final merged commit and its CI before submitting an assessment.

## Project and contribution process

Provides a Python web dashboard for Victron telemetry and configured controls.

- [Public repository and history](https://github.com/victron-venus/inverter-dashboard) provide source, commits and interim changes.
- [README](../README.md) describes installation, configuration and usage.
- [Contribution process](../CONTRIBUTING.md) documents reports, review, style and the policy to add automated tests for major changes.
- [Issues](https://github.com/victron-venus/inverter-dashboard/issues) and [pull requests](https://github.com/victron-venus/inverter-dashboard/pulls) provide searchable public discussion and change review.
- [Security policy](../SECURITY.md) documents confidential reporting, response goals, trust boundaries and delivery practices.

The root [LICENSE](../LICENSE) records the project license. Third-party components retain their own notices.

## Implementation and interfaces

- [src/inverter_dashboard](../src/inverter_dashboard)
- [local_config.example.py](../local_config.example.py)
- [contracts](../contracts)

Interface documentation must explain accepted configuration and inputs, outputs, failure handling and relevant permission boundaries. Verify it against the implementation when changing behavior; source links alone do not establish that every interface is documented.

## Build, test and analysis evidence

The local validation entry point is [scripts/ci.sh](../scripts/ci.sh). Its actual test and compiler/linter commands, not the presence of a workflow name, define the available coverage.

[GitHub Actions](https://github.com/victron-venus/inverter-dashboard/actions) provides run logs and results. The checked-in workflow definitions are:

- [auto-approve.yml](../.github/workflows/auto-approve.yml)
- [auto-merge.yml](../.github/workflows/auto-merge.yml)
- [ci.yml](../.github/workflows/ci.yml)
- [codeql.yml](../.github/workflows/codeql.yml)
- [coderabbit-autofix.yml](../.github/workflows/coderabbit-autofix.yml)
- [coderabbit-review.yml](../.github/workflows/coderabbit-review.yml)
- [dependency-review.yml](../.github/workflows/dependency-review.yml)
- [python-security.yml](../.github/workflows/python-security.yml)
- [quality-gate.yml](../.github/workflows/quality-gate.yml)
- [release-build.yml](../.github/workflows/release-build.yml)
- [release-pipeline.yml](../.github/workflows/release-pipeline.yml)
- [scorecard.yml](../.github/workflows/scorecard.yml)
- [trivy-fs.yml](../.github/workflows/trivy-fs.yml)

Do not equate a green metadata or release job with successful application tests. Record actual test results, coverage limitations and security-analysis results for the submitted revision. Test execution does not establish physical-device behavior.

## Items requiring explicit verification before submission

- Confirm the private reporting channel works and examine issue/advisory history. Historical response-time claims require actual reports and responses, including any reports outside GitHub.
- Obtain primary-developer attestations about secure-design and vulnerability-prevention knowledge; repository text cannot establish a person's knowledge.
- Verify every user-facing release has useful release notes and upgrade impact, and includes any assigned vulnerability identifiers for fixes.
- Review dependency, code-scanning and secret-scanning findings and their age. A workflow success result is not proof that all findings are resolved.
- Review the actual cryptographic libraries, protocols, key lengths, randomness, certificate checks and password storage applicable to this project. Do not copy another project's answers.
- Verify build reproducibility from source, test policy adherence in recent substantive changes, dynamic analysis and any manual-memory-code checks.
- Link only this project's real awarded badge once the assessment is accepted.

The live assessment, when created, is the source of truth for the badge level. Unverified criteria remain open.

## Release-note source

[CHANGELOG.md](../CHANGELOG.md) contains the current development line's change summary, upgrade impact and security notes. The release policy opts into commit-pinned notes; publication rejects missing or incomplete current-version sections. Historical release bodies still require a separate audit before claiming complete coverage.

The October 2026 maintenance imports the release identity and metadata parser
from `venus-os-ci-toolkit` revision `9dd211a`, including bounded TOML parsing and
source-bound release-note validation. Consumer release-contract suites verify
the imported engine. Workflow-validator refactoring preserves this repository's
existing policy; it does not imply all newer toolkit workflow guarantees are enabled.

Release guidance parsing also imports toolkit revision `ec99f37`: Markdown code
fences cannot provide or split the required version, Upgrade or Security headings.
The release contract suite tests matching fence markers and lengths, unclosed
examples, CRLF input and rejection before any remote publication writes.

The Bandit entry point ignores auto-discovered `.bandit` INI files by selecting
the operating system null device explicitly. CLI regressions verify that root,
nested and duplicate INI files cannot narrow the reviewed YAML scan policy.
Bandit emits an expected empty-INI warning; stderr remains visible.

Release guidance and validation helpers also import reviewed toolkit revision `cab6d07`: comments and empty code fences cannot satisfy upgrade/security guidance, while visible literal examples remain valid. Receipt size limits are enforced before parsing. The existing consumer workflow policy is retained.

[Toolkit revision `6f3bac8`](https://github.com/victron-venus/venus-os-ci-toolkit/commit/6f3bac867a101441e029ee7c20c5aa92541eb308) also makes same-level and higher-level ATX headings end release sections, including empty headings, while headings inside code or comments remain inert.

Release structure uses ATX headings (`#`, `##`, `###`), with up to three leading spaces. Setext-style or ambiguous text/comment/underline structures in the selected version are rejected; use ATX headings or a blank line before a thematic `---` separator. Literal fenced/indented examples and comments do not define sections.

Toolkit revision `4895f852f87ca1f27e2bc75e24c5c792e4f95aa6` includes verified staging cleanup and rejects release guidance made only of headings or separators. Regression tests cover retry, preservation of existing operator files, and visible guidance requirements.

## Additional source-analysis coverage

The [CodeQL workflow](../.github/workflows/codeql.yml) also analyzes src/inverter_dashboard/static/notifications-sw.js and the JavaScript delivered with the dashboard. Existing language analyses remain enabled. Each language reports a separate analysis category; review its completed run and findings for the submitted revision. A passing GitHub Code Quality check does not substitute for these security analyses.

Toolkit revision `d8089003f43637ee420fa4cbcccdf11f5d21e469` also rejects empty Markdown lists, task items and quotations as release guidance, while preserving literal examples and lists with substantive text.
