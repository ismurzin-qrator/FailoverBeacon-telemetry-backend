#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
image=$(sed -n 's/^ *image: \(clickhouse\/clickhouse-server:.*\)$/\1/p' compose.yaml)
run=beacon-it-$$
password=$(openssl rand -hex 12)
cleanup() { docker rm -fv "$run-ch" >/dev/null 2>&1 || true; docker network rm "$run" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker network create "$run" >/dev/null
docker run -d --name "$run-ch" --network "$run" -e CLICKHOUSE_PASSWORD="$password" \
    -v "$PWD/clickhouse/config.d/backups.xml:/etc/clickhouse-server/config.d/backups.xml:ro" \
    --tmpfs /backups:mode=1777 "$image" >/dev/null
for _ in $(seq 60); do
    docker exec "$run-ch" clickhouse-client --password "$password" -q 'SELECT 1' >/dev/null 2>&1 && break
    sleep 1
done

docker build -q -f ingest/Dockerfile -t beacon-it:local . >/dev/null
docker run --rm --network "$run" --user root -v "$PWD:/src" -w /src \
    -e CLICKHOUSE_TEST_HOST="$run-ch" -e CLICKHOUSE_TEST_PASSWORD="$password" \
    -e CLICKHOUSE_DATABASE="it_$$" -e UV_PROJECT_ENVIRONMENT=/tmp/venv \
    beacon-it:local sh -c 'uv sync --locked -q && uv run pytest -q tests/integration "$@"' -- "$@"
