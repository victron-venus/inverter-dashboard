# Architecture, security evidence and roadmap

## Getting started and architecture

Use [README](../README.md) for acquisition and operation,
[configuration](configuration.md) for settings and interfaces,
[development](development.md) for local changes, and
[architecture](architecture.md) for module boundaries.
The Python service joins MQTT telemetry, Home Assistant, gateway snapshots and
control requests. It serves the embedded Vue dashboard, WebSocket state and
notification endpoints. Notification storage, optional Web Push and opt-in
self-update each have separate failure and permission boundaries. Browser
acceptance must exercise the shipped static frontend, not a substitute page.

## Security assumptions and implemented controls

The service holds operator credentials and can issue equipment commands.
Keep configuration and local state private, constrain MQTT publishers and do
not expose unauthenticated control endpoints. Self-update executes trusted
upstream code and remains a distinct opt-in operation. The
[security policy](../SECURITY.md) defines confidential reporting and response.

The [TLS policy](tls-policy.md) describes the exact same-connection verified-chain
checks used for Home Assistant, the gateway, update metadata, MQTT, Web Push and
the HTTPS health probe. It also describes the HTTPS server's captured certificate
and key identity. RSA must be at least 2048 actual bits, DSA parameters at least
2048/224 bits, and supported elliptic curves at least 224 bits. TLS 1.2 or newer
and ordinary hostname/chain validation remain mandatory for TLS connections.
Intel macOS uses the native Security framework; other platforms use the maintained
cryptography library. Missing verified-chain support fails closed. Application
checks inspect public certificate metadata, not reimplement cryptographic
primitives. Plain HTTP/MQTT compatibility does not provide those TLS protections;
it belongs only inside an independently protected local deployment.

Web Push uses the maintained VAPID/Web Push libraries and their P-256 keys;
provider allowlists, DNS validation, disabled redirects and disabled environment
proxies bound the delivery path. TLS and key-generation randomness comes from
maintained operating-system-backed cryptographic libraries. Operator bearer
credentials are not a password-verifier database. Local API tokens and sensitive
configuration must be protected from untrusted local users.

## Validation and scope

Run [CONTRIBUTING](../CONTRIBUTING.md)'s checks. Local CI combines Bandit, Ruff and
Python tests. Tests include real TLS 1.2/1.3 handshake rejection before application
bytes, weak chain members and exact RSA boundaries, proxy behavior, MQTT/Web Push
requests, HTTPS identity loading and native macOS ownership/failure cases.
Dedicated Intel CI and frozen-binary smoke checks validate that platform rather
than inferring compatibility from Apple Silicon. Embedded-browser acceptance
checks are documented in [browser compatibility](browser-compatibility.md).
[RC fuzzing](fuzzing.md) is source-bound and separate from device acceptance.

The [changelog](../CHANGELOG.md) records delivered behavior, upgrade guidance and
security changes. Test counts and coverage belong to their exact source and run;
Python coverage does not establish frontend or whole-project 80% coverage.
Passing tests do not establish electrical safety or absence of vulnerabilities.

## Roadmap: October 2026–October 2027

Priorities are complete maintained-source coverage accounting, retained browser
and native-platform regression evidence, comprehensive input-boundary review,
and operator recovery drills. Preserve the fail-closed verified-chain behavior
when Python, HTTPX, Paho or cryptography change. A complete formal assurance case,
all-interface accessibility conformance and independently reproduced identical
release binaries have not been established.

There is currently no backup maintainer or one-week access recovery arrangement;
[governance](../GOVERNANCE.md) records this explicitly. The
[OpenSSF evidence index](openssf-evidence.md) points to the live self-assessment;
its awarded level does not replace target-system acceptance.

During this period, the project will not add unreviewed remote-control paths or
claim new hardware compatibility without target-system acceptance. Upstream
services and operating systems remain separate security responsibilities.

The [README](../README.md) links the live OpenSSF badge. Update achievement
links there within 48 hours of public recognition; the changelog records
software changes rather than the badge level.
