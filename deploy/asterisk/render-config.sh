#!/bin/bash
# Renders the parts of the Asterisk configuration that depend on the environment (ARI password,
# RTP port range, the external address for ICE on Docker Desktop) and hands over to the image's
# entrypoint, which fixes ownership and starts Asterisk.
set -e

GEN=/etc/asterisk/generated
mkdir -p "$GEN"
touch "$GEN/endpoints.conf"

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

chown -R asterisk:asterisk "$GEN" 2>/dev/null || true
exec /usr/local/bin/entrypoint.sh "$@"
