#!/usr/bin/env bash
# Generate a self-signed TLS cert for local inverter-dashboard and print macOS trust steps.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CERT_DIR="${CERT_DIR:-$ROOT/.certs}"
mkdir -p "$CERT_DIR"
CN="${TLS_CN:-inverter-dashboard.local}"

openssl req -x509 -newkey rsa:2048 \
  -keyout "$CERT_DIR/dashboard.key" \
  -out "$CERT_DIR/dashboard.crt" \
  -days 825 -nodes \
  -subj "/CN=$CN"

chmod 600 "$CERT_DIR/dashboard.key"

echo ""
echo "Certificate and key written to:"
echo "  $CERT_DIR/dashboard.crt"
echo "  $CERT_DIR/dashboard.key"
echo ""
echo "Trust the certificate on this Mac (Terminal):"
echo "  sudo security add-trusted-cert -d -r trustRoot \\"
echo "    -k /Library/Keychains/System.keychain \"$CERT_DIR/dashboard.crt\""
echo ""
echo "Or use Keychain Access: double-click dashboard.crt → Trust → Always Trust."
echo ""
echo "Run the dashboard with HTTPS (example port 8443):"
echo "  python \"$ROOT/server.py\" --ssl-cert \"$CERT_DIR/dashboard.crt\" \\"
echo "    --ssl-key \"$CERT_DIR/dashboard.key\" --port 8443"
