#!/bin/bash
# Renders the parts of the cloud voice Asterisk configuration that depend on the environment
# (ARI password, SIP port, RTP range, the external address, the Vapi trunk) and hands over to
# the image's entrypoint. Same shape as deploy/asterisk/render-config.sh; the SIP port is
# configurable so this container can run next to the local one (CLOUD_SIP_PORT).
set -e

GEN=/etc/asterisk/generated
mkdir -p "$GEN"
touch "$GEN/endpoints.conf"

ARI_PASSWORD="${ARI_PASSWORD:-trainer}"
SIP_PORT="${CLOUD_SIP_PORT:-5061}"
RTP_START="${CLOUD_RTP_START:-10200}"
RTP_END="${CLOUD_RTP_END:-10300}"
EXTERNAL_IP="${TELEPHONY_EXTERNAL_IP:-}"
LOCAL_NET="${TELEPHONY_LOCAL_NET:-172.16.0.0/12}"
VAPI_SIP_HOST="${VAPI_SIP_HOST:-sip.vapi.ai}"
VAPI_SIP_PORT="${VAPI_SIP_PORT:-5060}"
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
  echo "bind=0.0.0.0:$SIP_PORT"
  echo ""
  echo "[transport-tcp]"
  echo "type=transport"
  echo "protocol=tcp"
  echo "bind=0.0.0.0:$SIP_PORT"
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

# The trunk to Vapi: no registration, no authentication; G.711 both ways (the browser leg is
# Opus, Asterisk transcodes in the bridge). Requests from Vapi's addresses are matched to it.
{
  echo "[vapi]"
  echo "type=endpoint"
  echo "context=vapi-in"
  echo "transport=transport-udp"
  echo "disallow=all"
  echo "allow=ulaw,alaw"
  echo "aors=vapi"
  echo "direct_media=no"
  echo "rtp_symmetric=yes"
  echo "force_rport=yes"
  echo "rewrite_contact=yes"
  echo "language=ru"
  if [ -n "$EXTERNAL_IP" ]; then
    echo "from_domain=$EXTERNAL_IP"
  fi
  echo ""
  echo "[vapi]"
  echo "type=aor"
  echo "contact=sip:$VAPI_SIP_HOST:$VAPI_SIP_PORT"
  echo "qualify_frequency=0"
  echo ""
  echo "[vapi]"
  echo "type=identify"
  echo "endpoint=vapi"
  echo "match=$VAPI_SIP_HOST"
} > "$GEN/trunk.conf"

# The trunk to MultiFon (МультиФон Бизнес, MegaFon): the trainee's own phone rings next to the
# softphone, and a call to the MultiFon number picks up the trainee's ringing call. Asterisk
# registers itself (MultiFon sends incoming calls only to a registered contact); «line» ties
# those calls to the endpoint. Empty file when no account is configured: the feature is off.
MULTIFON_USER="${MULTIFON_USER:-}"
MULTIFON_PASSWORD="${MULTIFON_PASSWORD:-}"
MULTIFON_DOMAIN="${MULTIFON_DOMAIN:-multifon.ru}"
MULTIFON_PROXY="${MULTIFON_PROXY:-sbc.megafon.ru}"
MULTIFON_PROXY_PORT="${MULTIFON_PROXY_PORT:-5060}"
: > "$GEN/multifon.conf"
: > "$GEN/extensions-globals.conf"
if [ -n "$MULTIFON_USER" ] && [ -n "$MULTIFON_PASSWORD" ]; then
  PROXY_URI="sip:$MULTIFON_PROXY:$MULTIFON_PROXY_PORT\\;lr"
  {
    echo "[multifon-auth]"
    echo "type=auth"
    echo "auth_type=userpass"
    echo "username=$MULTIFON_USER"
    echo "password=$MULTIFON_PASSWORD"
    echo ""
    echo "[multifon]"
    echo "type=aor"
    echo "contact=sip:$MULTIFON_DOMAIN"
    echo "qualify_frequency=0"
    echo ""
    echo "[multifon]"
    echo "type=endpoint"
    echo "context=multifon-in"
    echo "transport=transport-udp"
    echo "disallow=all"
    echo "allow=alaw,ulaw"
    echo "aors=multifon"
    echo "outbound_auth=multifon-auth"
    echo "outbound_proxy=$PROXY_URI"
    echo "from_user=$MULTIFON_USER"
    echo "from_domain=$MULTIFON_DOMAIN"
    echo "direct_media=no"
    echo "rtp_symmetric=yes"
    echo "force_rport=yes"
    echo "rewrite_contact=yes"
    echo "dtmf_mode=rfc4733"
    echo "language=ru"
    echo ""
    echo "[multifon-reg]"
    echo "type=registration"
    echo "transport=transport-udp"
    echo "outbound_auth=multifon-auth"
    echo "outbound_proxy=$PROXY_URI"
    echo "server_uri=sip:$MULTIFON_DOMAIN"
    echo "client_uri=sip:$MULTIFON_USER@$MULTIFON_DOMAIN"
    echo "contact_user=$MULTIFON_USER"
    echo "expiration=300"
    echo "retry_interval=30"
    echo "forbidden_retry_interval=300"
    echo "max_retries=100000"
    echo "line=yes"
    echo "endpoint=multifon"
  } > "$GEN/multifon.conf"
  echo "MULTIFON_NUMBER=$MULTIFON_USER" > "$GEN/extensions-globals.conf"
fi

chown -R asterisk:asterisk "$GEN" 2>/dev/null || true
exec /usr/local/bin/entrypoint.sh "$@"
