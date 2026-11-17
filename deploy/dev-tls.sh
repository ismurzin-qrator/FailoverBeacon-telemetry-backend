#!/usr/bin/env bash
# usage: deploy/dev-tls.sh IP:203.0.113.10 [DNS:beacon.test ...]
set -euo pipefail
[ $# -gt 0 ] || { echo "usage: $0 IP:<address>|DNS:<name> ..." >&2; exit 1; }
cd "$(dirname "$0")/.."
mkdir -p -m 700 secrets/tls
cd secrets/tls
san=$(IFS=,; echo "$*")
openssl req -x509 -newkey rsa:2048 -nodes -days 825 -subj "/CN=Beacon Test CA" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -keyout ca.key -out ca.crt 2>/dev/null
openssl req -newkey rsa:2048 -nodes -subj "/CN=${1#*:}" -keyout server.key -out server.csr 2>/dev/null
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 825 \
    -extfile <(printf 'subjectAltName=%s\nextendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n' "$san") \
    -out server.crt 2>/dev/null
rm -f server.csr ca.srl
chmod 600 ca.key server.key
echo "wrote secrets/tls/{ca.crt,server.crt,server.key} for $san"
