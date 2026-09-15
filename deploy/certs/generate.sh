#!/bin/sh
# Generates an internal CA and a server certificate for nginx.
# Usage: deploy/certs/generate.sh [output-dir] [hosts]
#   output-dir  where to write ca.crt, server.crt, server.key (default: this directory)
#   hosts       comma-separated host names / IPs for the certificate (default: localhost,127.0.0.1)
# Existing certificates are kept; delete them to regenerate.
set -eu

OUT_DIR="${1:-$(dirname "$0")}"
HOSTS="${2:-${TLS_HOSTS:-localhost,127.0.0.1}}"
DAYS=825

mkdir -p "$OUT_DIR"
if [ -f "$OUT_DIR/server.crt" ] && [ -f "$OUT_DIR/server.key" ]; then
  echo "Сертификаты уже есть в $OUT_DIR, пропускаю"
  exit 0
fi

SAN=""
for host in $(echo "$HOSTS" | tr ',' ' '); do
  case "$host" in
    *[!0-9.]*) entry="DNS:$host" ;;
    *)         entry="IP:$host" ;;
  esac
  SAN="${SAN:+$SAN,}$entry"
done

cat > "$OUT_DIR/server.cnf" <<CNF
[req]
distinguished_name = dn
req_extensions = ext
prompt = no
[dn]
CN = $(echo "$HOSTS" | cut -d, -f1)
O = DDS-112 Trainer
[ext]
subjectAltName = $SAN
extendedKeyUsage = serverAuth
CNF

# Internal certificate authority (import ca.crt into the browser to remove the warning).
if [ ! -f "$OUT_DIR/ca.key" ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days "$DAYS" -sha256 \
    -subj "/CN=DDS-112 Trainer Internal CA/O=DDS-112 Trainer" \
    -keyout "$OUT_DIR/ca.key" -out "$OUT_DIR/ca.crt" 2>/dev/null
fi

openssl req -new -newkey rsa:2048 -nodes -sha256 \
  -config "$OUT_DIR/server.cnf" \
  -keyout "$OUT_DIR/server.key" -out "$OUT_DIR/server.csr" 2>/dev/null
openssl x509 -req -days "$DAYS" -sha256 \
  -in "$OUT_DIR/server.csr" -CA "$OUT_DIR/ca.crt" -CAkey "$OUT_DIR/ca.key" -CAcreateserial \
  -extfile "$OUT_DIR/server.cnf" -extensions ext \
  -out "$OUT_DIR/server.crt" 2>/dev/null
rm -f "$OUT_DIR/server.csr"
chmod 600 "$OUT_DIR"/*.key
echo "Сертификаты записаны в $OUT_DIR для: $HOSTS"
