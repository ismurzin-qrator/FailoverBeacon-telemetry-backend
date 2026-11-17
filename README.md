# Telemetry backend

Accepts uploads in the format of the [telemetry schema](https://github.com/ismurzin-qrator/FailoverBeacon-telemetry-schema), stores them in ClickHouse and shows them in Grafana.

## Layout

| Path | Contents |
| --- | --- |
| `src/beacon/` | The ingest service and the `beacon` command, with the migrations, the registry format and the MCC table |
| `src/beacon/telemetry-schema/` | The schema at a tag; `scripts/copy-schema.sh <repository> <tag>` replaces it |
| `ingest/`, `clickhouse/`, `nginx/`, `grafana/` | Configuration of each service in `compose.yaml` |
| `grafana/dashboards/` | Dashboards, exported from Grafana |
| `deploy/` | Host setup, deploy, dashboard export, secrets, the geo update timer, an example registry |
| `scripts/` | Schema copy, integration tests, load test |

## Run

On any Docker host:

```bash
deploy/secrets.sh
deploy/dev-tls.sh DNS:localhost
docker compose up -d --build
docker compose run --rm tools beacon migrate
docker compose run --rm tools beacon registry load
docker compose run --rm tools beacon dashboards import
```

The stack reads `deploy/registry.example.json` unless `BEACON_REGISTRY_FILE` in `.env` names another registry.

## Deploy

An installation is a directory with `registry.json` and `deploy.env`:

```bash
HOST=root@203.0.113.10
TLS_NAMES="IP:203.0.113.10"
```

A new Debian host is prepared once:

```bash
ssh <user>@<host> 'bash -s' < deploy/host-setup.sh
```

The host's SSH key needs read access to this repository, for example as a deploy key. Each release is a pushed tag:

```bash
deploy/deploy.sh <installation dir> <git tag>
```

The host checks out the tag in `/opt/beacon`, keeping `secrets/` and `.env`. Then it creates missing secrets, backs up the database and the dashboards, migrates, loads the registry, recreates the services and creates the dashboards Grafana lacks, stopping at the first failed step. Until `secrets/tls/` holds the domain's certificate, a test CA issues one for `TLS_NAMES`.

## Commands

They run in the `tools` service: `docker compose run --rm tools beacon <command>`.

| Command | Does |
| --- | --- |
| `migrate` | Applies new migrations, then checks that every schema field has a column of the matching type. An applied migration must not change |
| `registry load` | Copies the projects into ClickHouse for Grafana |
| `geo update` | Installs this month's DB-IP Lite databases (CC BY 4.0); ingest picks them up within a minute |
| `replay` | Stores uploads kept as `schema_newer` once the backend knows their schema |
| `schema check` | Compares the schema copy with its `SHA256SUMS` |
| `dashboards import` | Creates the dashboards of `grafana/dashboards/` that Grafana lacks |
| `dashboards export` | Prints every dashboard as one JSON object |

Migrations only add: `CREATE … IF NOT EXISTS`, `ADD COLUMN`, `MODIFY TTL`.

## Keys

```bash
uv run beacon keys add --registry registry.json --project acme --name "Acme" --package com.acme.app
uv run beacon keys disable --registry registry.json <key>
```

`add` creates the project if it is new and prints the beacon URL; `--package '*'` accepts any app. After the deploy, a disabled key gets 410.

## Grafana

```bash
ssh -L 3000:127.0.0.1:3000 <user>@<host>
```

Then http://localhost:3000, user `admin`, password in `secrets/grafana_admin_password`. Dashboards are edited and saved in Grafana. To keep the changes in git:

```bash
deploy/export-dashboards.sh <installation dir>
```

It replaces `grafana/dashboards/` with what Grafana has; commit the result. A deploy never overwrites a dashboard Grafana already has, so a dashboard deleted in Grafana but still in git comes back.

## Checks

```bash
uv run ruff format --check . && uv run ruff check . && uv run mypy src scripts/load_test.py && uv run pytest
```

On a Docker host, `scripts/integration.sh` runs the integration tests against a throwaway ClickHouse.

The load test sends rising numbers of concurrent uploads, best from the host itself:

```bash
docker run --rm --network host -v "$PWD:/src" beacon:local python /src/scripts/load_test.py http://127.0.0.1:8080/v1/beacon/<key> --levels 32,64,128
```

## License

[MIT](LICENSE)
