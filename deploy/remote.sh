#!/usr/bin/env bash
set -euo pipefail
cd /opt/beacon
deploy/secrets.sh
grep -q '^BEACON_REGISTRY_FILE=' .env || echo 'BEACON_REGISTRY_FILE=/opt/beacon-deploy/registry.json' >> .env
[ -s secrets/tls/server.crt ] || deploy/dev-tls.sh $TLS_NAMES

ch() { user=$1; shift
    docker compose exec -T clickhouse clickhouse-client --user "$user" \
        --password "$(cat "secrets/clickhouse_${user}_password")" "$@" </dev/null; }

docker compose build -q
if docker compose ps --status running --services | grep -qx grafana; then
    docker compose run --rm -T tools beacon dashboards export > /opt/beacon-deploy/dashboards.json
fi
# Recreated, since a running container keeps the replaced files. ClickHouse first, as it rereads users.d.
docker compose up -d --force-recreate clickhouse
for _ in $(seq 60); do ch migrator -q 'SELECT 1' >/dev/null 2>&1 && break; sleep 1; done
ch migrator -q 'SELECT 1' >/dev/null

exists=$(ch migrator -q 'EXISTS DATABASE telemetry')
if [ "$exists" = 1 ]; then
    name="before-$(date -u +%Y%m%dT%H%M%SZ).zip"
    ch backup -q "BACKUP DATABASE telemetry TO Disk('backups', '$name')" --format TSV
    docker compose exec -T clickhouse find /backups -maxdepth 1 -name 'before-*.zip' ! -name "$name" -delete </dev/null
fi

docker compose run --rm -T tools beacon schema check
docker compose run --rm -T tools beacon migrate
docker compose run --rm -T tools beacon registry load
docker compose up -d --force-recreate --remove-orphans
docker compose run --rm -T tools beacon dashboards import

for _ in $(seq 60); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/healthz)" = 200 ] && break
    sleep 1
done
curl -sf http://127.0.0.1:8080/healthz >/dev/null || { echo "ingest is not healthy" >&2; exit 1; }
curl -skf https://127.0.0.1/v1/health >/dev/null || { echo "nginx does not reach ingest" >&2; exit 1; }

install -m 644 deploy/systemd/beacon-geo-update.service deploy/systemd/beacon-geo-update.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now beacon-geo-update.timer >/dev/null
docker compose run --rm -T tools sh -c 'test -s /var/lib/beacon/geo/country.mmdb' || systemctl start beacon-geo-update.service

docker image prune -f >/dev/null
echo "healthy; backups: $(docker compose exec -T clickhouse ls /backups </dev/null | tr '\n' ' ')"
