# Asterisk of the cloud voice (plan/track-c-vapi.md): the same image and shared configuration
# as deploy/asterisk (WebRTC softphones, ARI), plus a SIP trunk to Vapi and the dialplan
# that bridges the trainee with the caller Vapi plays. Build context is deploy/.
FROM andrius/asterisk:22.10.1_debian-trixie

COPY asterisk/asterisk.conf asterisk/modules.conf asterisk/logger.conf asterisk/http.conf \
     asterisk/rtp.conf asterisk/ari.conf /etc/asterisk/
COPY asterisk-cloud/pjsip.conf asterisk-cloud/extensions.conf /etc/asterisk/
COPY asterisk-cloud/render-config.sh /usr/local/bin/render-config.sh
RUN chmod +x /usr/local/bin/render-config.sh \
    && mkdir -p /etc/asterisk/generated /var/spool/asterisk/recording \
    && touch /etc/asterisk/generated/endpoints.conf \
    && chown -R asterisk:asterisk /etc/asterisk/generated /var/spool/asterisk/recording

ENTRYPOINT ["/usr/local/bin/render-config.sh"]
CMD ["/usr/sbin/asterisk", "-f", "-T", "-U", "asterisk", "-p", "-n"]
