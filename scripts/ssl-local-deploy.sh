#!/usr/bin/env bash
# Generate a self-signed TLS cert for local inverter-dashboard and print macOS trust steps.
# Includes Subject Alternative Name (SAN); Chrome/Safari require SAN for hostname validation (CN alone is not enough).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CERT_DIR="${CERT_DIR:-$ROOT/.certs}"
mkdir -p "$CERT_DIR"
CN="${TLS_CN:-inverter-dashboard.local}"

OPENSSL_CFG=$(mktemp)
trap 'rm -f "$OPENSSL_CFG"' EXIT

# x509_extensions applies extensions to the self-signed certificate (not only CSR v3_req).
cat >"$OPENSSL_CFG" <<EOF
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
x509_extensions = v3_cert

[dn]
CN = $CN

[v3_cert]
subjectAltName = @alt_names
extendedKeyUsage = serverAuth
keyUsage = digitalSignature, keyEncipherment

[alt_names]
DNS.1 = $CN
DNS.2 = localhost
IP.1 = 127.0.0.1
EOF

openssl req -x509 -newkey rsa:2048 \
  -keyout "$CERT_DIR/dashboard.key" \
  -out "$CERT_DIR/dashboard.crt" \
  -days 825 -nodes \
  -config "$OPENSSL_CFG"

chmod 600 "$CERT_DIR/dashboard.key"

echo ""
echo "Certificate and key written to:"
echo "  $CERT_DIR/dashboard.crt"
echo ""
echo "Trust the certificate on this Mac (Terminal):"
echo "  sudo security add-trusted-cert -d -r trustRoot \\"
echo "    -k /Library/Keychains/System.keychain \"$CERT_DIR/dashboard.crt\""
echo ""
echo "Or use Keychain Access: double-click dashboard.crt → Trust → Always Trust."
echo ""
echo "Regenerate after changing TLS_CN=... ; then redeploy postinstall.sh / copy new cert to NAS config."
echo ""
echo "Run the dashboard with HTTPS (example port 8443):"
echo "  python \"$ROOT/server.py\" --ssl-cert \"$CERT_DIR/dashboard.crt\" \\"
echo "    --ssl-key \"$CERT_DIR/dashboard.key\" --port 8443"
