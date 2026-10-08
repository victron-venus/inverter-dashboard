# HTTPS client certificate policy

The Home Assistant client, gateway snapshot/command client and update-metadata
client use the shared policy in `src/inverter_dashboard/tls_policy.py`. Before
HTTP headers or bodies are sent, it checks the complete certificate chain that
TLS verified for that same connection, including the selected trust anchor.
Ordinary chain and hostname validation must succeed first. There is no second
connection, alternate trust decision or change to the operating system trust store.

## Accepted keys and failure behavior

These clients require TLS 1.2 or later and retain stricter runtime security
settings. Every certificate must meet these public-key minima:

- RSA: an actual modulus of at least 2048 bits.
- Elliptic curves: at least 224 bits.
- DSA: a modulus of at least 2048 bits and subgroup of at least 224 bits.
- Ed25519 and Ed448 are supported by the cryptography-backed parser.

Unknown keys, malformed certificates and runtimes that cannot expose the verified
chain fail closed. Intel macOS uses Apple's Security framework to inspect RSA
and elliptic-curve keys; other key types fail closed on that platform. This keeps
the existing Intel binary profile, which does not include `cryptography`.
Other platforms use the project's existing `cryptography` dependency.

OpenSSL security level 2 alone accepts some 2047-bit RSA keys. The additional
check enforces the exact boundary. On rejection, callers retain their existing
connection-error behavior and do not send their HTTP credentials or body to that
TLS peer. Reissue weak private roots, intermediates or server certificates with
supported keys, then update the configured CA bundle. Disabling verification is
not a supported migration path. Plain HTTP retains its existing behavior.

## Certificate authorities and proxies

HTTPX retains its normal certifi trust bundle and `SSL_CERT_FILE`/`SSL_CERT_DIR`
selection. `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` and `NO_PROXY` routing retains
HTTPX's semantics, including explicit bypasses. For an HTTPS proxy, the proxy's
own certificate chain is checked before CONNECT or proxy credentials are sent;
the destination chain is checked separately before the HTTP request is sent.
The proxy context retains HTTP Core's existing trust-root selection. A plain
HTTP proxy does not encrypt CONNECT metadata or proxy authentication; use an
HTTPS proxy where those need transport protection.

Gateway redirects remain disabled. The shared client factory does not change
the other callers' redirect settings. CA settings apply when a client is created;
restart the relevant client or process after changing a CA bundle.

## Validation and maintenance boundaries

`tests/test_tls_policy.py` performs actual TLS 1.2 and 1.3 handshakes over both
stdlib sockets and HTTPX memory BIOs. It covers weak leaf, intermediate and root
keys, the 2047-bit RSA boundary, strong controls, wrong names, unknown CAs,
HTTP/HTTPS proxies and environment-routing parity. Negative cases assert that
application data did not reach the rejected peer. Only disposable local test
certificates and synthetic credentials are used.

`tests/test_darwin_certificate.py` exercises native key parsing, ownership and
error paths, plus actual TLS connections without requiring cryptography.
The dedicated Intel macOS CI job tests the production dispatch with the Intel
dependency profile. A test on Apple Silicon alone does not establish Intel
compatibility. Release builds additionally perform the existing frozen-binary
smoke checks.

CPython 3.12 exposes its verified chain through a private SSL API; newer runtimes
may expose it publicly. HTTPX's environment-proxy mapping helper is also private.
The dependency lock and regression tests make both boundaries explicit. Rerun
these tests after Python, HTTPX or HTTP Core upgrades; an unavailable chain API
must remain a connection failure.

This policy covers only the HTTPX clients listed above. MQTT, Web Push, inbound
TLS, external TLS terminators and hardware behavior have separate boundaries.
These tests alone do not establish a whole-project OpenSSF badge or validate an
operator's deployment.
