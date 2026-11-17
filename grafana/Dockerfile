FROM grafana/grafana:13.3.1@sha256:0615ec4fbd4072004b7d86b46ded63013a74b7d6eb1119273dd89e8fa25c13cf
# /var/lib/grafana is a volume and would hide plugins installed there.
ENV GF_PATHS_PLUGINS=/opt/grafana-plugins
USER root
RUN mkdir -p /opt/grafana-plugins \
    && grafana cli --pluginsDir /opt/grafana-plugins plugins install grafana-clickhouse-datasource 4.23.5 \
    && chown -R grafana /opt/grafana-plugins
COPY grafana/provisioning /etc/grafana/provisioning
USER grafana
