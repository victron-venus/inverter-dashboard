# TLS certificate policy

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

### MQTT, Web Push and the loopback health probe

The same verified-chain check also runs before MQTT CONNECT credentials, Web
Push authorization/payloads and the HTTPS health request are sent. MQTT retains
Paho's CA-file/default-root selection and verification flags. Plain MQTT remains
unchanged. Web Push retains its provider allowlist, validated DNS addresses,
timeouts, disabled redirects and disabled environment-proxy support. The health
probe remains bound to loopback and trusts only its configured certificate file.

### Built-in HTTPS server

When `--ssl-cert` is supplied, the built-in server checks every certificate in
that PEM file against the same key minima before loading the identity or opening
the listener. A separately supplied `--ssl-key` or a private key in the combined
PEM file is supported. OpenSSL still checks that the key matches the certificate.

The loader captures the certificate/key once and passes an immutable snapshot
to [Uvicorn's SSL context factory](https://uvicorn.dev/settings/#https). This
prevents file replacement between the key-size check and OpenSSL loading from
substituting an unchecked identity. Temporary files are mode 0600 in a private
0700 directory and are removed after loading, including on errors. Existing
Uvicorn TLS options are retained, with TLS 1.2 and security level 2 as minimums.
Operators must reissue weak server chains before upgrading and restart the
server when rotating certificates. External reverse proxies have their own TLS
configuration and are outside this loader.

### Regression coverage

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

`tests/test_additional_tls.py` tests actual MQTT CONNECT and encrypted Web Push
requests, including hostname and CA failures. `tests/test_server_tls.py` covers
server startup rejection, real TLS 1.2/1.3 requests, combined/separate PEM files,
concurrent identity replacement, failed-load cleanup and the real health probe.

CPython 3.12 exposes its verified chain through a private SSL API; newer runtimes
may expose it publicly. HTTPX's environment-proxy mapping helper is also private.
The dependency lock and regression tests make both boundaries explicit. Rerun
these tests after Python, HTTPX or HTTP Core upgrades; an unavailable chain API
must remain a connection failure.

This policy covers the application paths listed above. Packaging tools, external
TLS terminators and hardware behavior have separate boundaries.
These tests alone do not establish a whole-project OpenSSF badge or validate an
operator's deployment.
