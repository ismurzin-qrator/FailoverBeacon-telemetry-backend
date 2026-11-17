#!/usr/bin/env bash
# usage: deploy/export-dashboards.sh <deployment dir>
set -euo pipefail
# shellcheck source=/dev/null
. "$1/deploy.env"
cd "$(dirname "$0")/.."
ssh "$HOST" 'cd /opt/beacon && docker compose run --rm -T tools beacon dashboards export' </dev/null |
    uv run --quiet beacon dashboards save grafana/dashboards
