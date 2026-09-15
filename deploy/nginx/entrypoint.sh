#!/bin/sh
# Generates TLS certificates on first start (kept in the "certs" volume).
set -e
/usr/local/bin/generate-certs.sh /etc/nginx/certs "${TLS_HOSTS:-localhost,127.0.0.1}"
