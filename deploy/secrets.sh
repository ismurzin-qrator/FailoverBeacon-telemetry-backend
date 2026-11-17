#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p -m 700 secrets
touch .env
grep -v '^CLICKHOUSE_' .env > .env.new || true
mv .env.new .env
chmod 600 .env
for name in clickhouse_ingest clickhouse_grafana clickhouse_migrator clickhouse_backup grafana_admin; do
    file="secrets/${name}_password"
    if [ ! -s "$file" ]; then
        openssl rand -hex 24 | tr -d '\n' > "$file"
        echo "created $file"
    fi
    chmod 644 "$file"  # for the containers' users. secrets/ is 700
done
for user in ingest grafana migrator backup; do
    hash=$(openssl dgst -sha256 -r < "secrets/clickhouse_${user}_password" | cut -d' ' -f1)
    printf 'CLICKHOUSE_%s_PASSWORD_SHA256=%s\n' "$(echo "$user" | tr '[:lower:]' '[:upper:]')" "$hash" >> .env
done
