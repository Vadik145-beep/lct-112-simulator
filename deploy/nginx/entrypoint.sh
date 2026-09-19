#!/bin/sh
# Generates TLS certificates on first start (kept in the "certs" volume) and, when PUBLIC_HOST
# has a Let's Encrypt certificate in the "letsencrypt" volume, the server block that serves
# the stand under that name with it.
set -e
/usr/local/bin/generate-certs.sh /etc/nginx/certs "${TLS_HOSTS:-localhost,127.0.0.1}"

PUBLIC_DIR=/etc/nginx/conf.d/public
mkdir -p "$PUBLIC_DIR"
rm -f "$PUBLIC_DIR"/*.conf
LIVE="/etc/nginx/letsencrypt/live/${PUBLIC_HOST:-}"
if [ -n "${PUBLIC_HOST:-}" ] && [ -f "$LIVE/fullchain.pem" ] && [ -f "$LIVE/privkey.pem" ]; then
  cat > "$PUBLIC_DIR/public.conf" <<CONF
server {
    listen 443 ssl;
    http2 on;
    server_name $PUBLIC_HOST;

    ssl_certificate $LIVE/fullchain.pem;
    ssl_certificate_key $LIVE/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;

    include /etc/nginx/locations.conf;
}
CONF
  echo "Публичный хост $PUBLIC_HOST: сертификат Let's Encrypt подключён"
elif [ -n "${PUBLIC_HOST:-}" ]; then
  echo "Публичный хост $PUBLIC_HOST: сертификата в $LIVE нет, работает только внутренний"
fi
