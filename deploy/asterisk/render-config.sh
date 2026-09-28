#!/bin/bash
# Renders the parts of the Asterisk configuration that depend on the environment (ARI password,
# RTP port range, the external address for ICE on Docker Desktop) and hands over to the image's
# entrypoint, which fixes ownership and starts Asterisk.
set -e

GEN=/etc/asterisk/generated
mkdir -p "$GEN"
touch "$GEN/endpoints.conf"
# Emptied on every start: removing the trunk variables switches the trunk off.
: > "$GEN/trunk.conf"

# TRUNK_ON: the trunk block below is rendered (the same condition), calls of a lesson with
# calls to the phone then ring the trainee's own phone through it (trainee-targets).
TRUNK_ON=""
if [ -n "${TELEPHONY_TRUNK_USER:-}" ] && [ -n "${TELEPHONY_TRUNK_PASSWORD:-}" ]   && [ -n "${TELEPHONY_TRUNK_DOMAIN:-}" ]; then
  TRUNK_ON=1
fi
{
  echo "[globals]"
  echo "TRUNK_ON=$TRUNK_ON"
  echo "TRUNK_INBOUND=${TELEPHONY_TRUNK_INBOUND:-}"
} > "$GEN/globals.conf"

ARI_PASSWORD="${ARI_PASSWORD:-trainer}"
RTP_START="${TELEPHONY_RTP_START:-10000}"
RTP_END="${TELEPHONY_RTP_END:-10100}"
EXTERNAL_IP="${TELEPHONY_EXTERNAL_IP:-}"
LOCAL_NET="${TELEPHONY_LOCAL_NET:-172.16.0.0/12}"
CONTAINER_IP="$(hostname -i | cut -d' ' -f1)"

cat > "$GEN/ari-users.conf" <<CONF
[trainer]
type = user
read_only = no
password = $ARI_PASSWORD
CONF

{
  echo "[general]"
  echo "rtpstart=$RTP_START"
  echo "rtpend=$RTP_END"
  echo "icesupport=yes"
  echo "strictrtp=yes"
  if [ -n "$EXTERNAL_IP" ]; then
    # Docker Desktop: the container address is unreachable from the browser, so ICE offers
    # the address of the machine (ports of the range are published) next to the local one.
    echo ""
    echo "[ice_host_candidates]"
    echo "$CONTAINER_IP => $EXTERNAL_IP,include_local_address"
  fi
} > "$GEN/rtp.conf"

{
  echo "[transport-ws]"
  echo "type=transport"
  echo "protocol=ws"
  echo "bind=0.0.0.0"
  echo ""
  echo "[transport-udp]"
  echo "type=transport"
  echo "protocol=udp"
  echo "bind=0.0.0.0:5060"
  echo ""
  echo "[transport-tcp]"
  echo "type=transport"
  echo "protocol=tcp"
  echo "bind=0.0.0.0:5060"
  if [ -n "$EXTERNAL_IP" ]; then
    for t in transport-ws transport-udp transport-tcp; do
      echo ""
      echo "[$t](+)"
      echo "external_media_address=$EXTERNAL_IP"
      echo "external_signaling_address=$EXTERNAL_IP"
      echo "local_net=$LOCAL_NET"
    done
  fi
} > "$GEN/transports.conf"

# Внешний SIP-транк оператора связи: включается только если заданы логин и пароль.
# Значения живут в .env на сервере, в репозиторий они не попадают.
TRUNK_USER="${TELEPHONY_TRUNK_USER:-}"
TRUNK_PASSWORD="${TELEPHONY_TRUNK_PASSWORD:-}"
TRUNK_DOMAIN="${TELEPHONY_TRUNK_DOMAIN:-}"
TRUNK_PROXY="${TELEPHONY_TRUNK_PROXY:-}"
TRUNK_CODECS="${TELEPHONY_TRUNK_CODECS:-alaw,ulaw}"

if [ -n "$TRUNK_USER" ] && [ -n "$TRUNK_PASSWORD" ] && [ -n "$TRUNK_DOMAIN" ]; then
  PROXY_LINE=""
  if [ -n "$TRUNK_PROXY" ]; then
    PROXY_LINE="outbound_proxy=sip:$TRUNK_PROXY\\;lr"
  fi
  {
    echo "[trunk-auth]"
    echo "type=auth"
    echo "auth_type=userpass"
    echo "username=$TRUNK_USER"
    echo "password=$TRUNK_PASSWORD"
    echo ""
    echo "[trunk-reg]"
    echo "type=registration"
    echo "transport=transport-udp"
    echo "outbound_auth=trunk-auth"
    echo "server_uri=sip:$TRUNK_DOMAIN"
    echo "client_uri=sip:$TRUNK_USER@$TRUNK_DOMAIN"
    echo "expiration=180"
    echo "retry_interval=60"
    [ -n "$PROXY_LINE" ] && echo "$PROXY_LINE"
    echo ""
    echo "[trunk-aor]"
    echo "type=aor"
    echo "contact=sip:${TRUNK_PROXY:-$TRUNK_DOMAIN}"
    echo ""
    echo "[trunk]"
    echo "type=endpoint"
    echo "transport=transport-udp"
    echo "context=trunk-in"
    echo "disallow=all"
    echo "allow=$TRUNK_CODECS"
    echo "outbound_auth=trunk-auth"
    echo "aors=trunk-aor"
    echo "from_user=$TRUNK_USER"
    echo "from_domain=$TRUNK_DOMAIN"
    echo "direct_media=no"
    echo "rtp_symmetric=yes"
    echo "force_rport=yes"
    echo "rewrite_contact=yes"
    echo "language=ru"
    [ -n "$PROXY_LINE" ] && echo "$PROXY_LINE"
    echo ""
    echo "[trunk-identify]"
    echo "type=identify"
    echo "endpoint=trunk"
    echo "match=${TRUNK_PROXY%%:*}"
    echo "match=$TRUNK_DOMAIN"
  } > "$GEN/trunk.conf"
fi

chown -R asterisk:asterisk "$GEN" 2>/dev/null || true
exec /usr/local/bin/entrypoint.sh "$@"
