# Telemetry backend

Accepts uploads in the format of the [telemetry schema](https://github.com/ismurzin-qrator/FailoverBeacon-telemetry-schema), stores them in ClickHouse and shows them in Grafana.

## Layout

| Path | Contents |
| --- | --- |
| `src/beacon/` | The ingest service and the `beacon` command, with the migrations, the registry format and the MCC table |
| `src/beacon/telemetry-schema/` | The schema at a tag; `scripts/copy-schema.sh <repository> <tag>` replaces it |
| `ingest/`, `clickhouse/`, `nginx/`, `grafana/` | Configuration of each service in `compose.yaml` |
| `grafana/dashboards/` | Dashboards, exported from Grafana |
| `registry.example.json` | Projects and their keys |
| `scripts/` | Schema copy, integration tests, load test |

## Run

Needs Docker with Compose. Copy `.env.example` to `.env` and set the passwords, then:

```bash
docker compose up -d --build clickhouse ingest grafana
docker compose run --rm tools beacon migrate
docker compose run --rm tools beacon registry load
docker compose run --rm tools beacon dashboards import
```

Ingest listens on http://127.0.0.1:8080, Grafana on http://127.0.0.1:3000 (user `admin`). To fill the dashboards with the schema samples:

```bash
uv run scripts/load_test.py http://127.0.0.1:8080/v1/beacon/bbbbbbbbbbbbbbbbbbbbbbbbbb --levels 1 --seconds 5
```

## Server

nginx serves `https://<host>/v1/beacon/<key>` with the certificate chain and key in `tls/server.crt` and `tls/server.key`. It uses the host network, so it runs on Linux. Point `BEACON_REGISTRY_FILE` at the server's registry and start every service:

```bash
docker compose up -d --build
```

Then run the three commands above. An upgrade checks out the new version and runs:

```bash
docker compose build
docker compose run --rm tools beacon backup
docker compose run --rm tools beacon migrate
docker compose up -d --force-recreate
docker compose run --rm tools beacon registry load
docker compose run --rm tools beacon dashboards import
```

`beacon geo update` once a month adds countries and networks to new events.

## Commands

They run in the `tools` service: `docker compose run --rm tools beacon <command>`.

| Command | Does |
| --- | --- |
| `migrate` | Applies new migrations, then checks that every schema field has a column of the matching type. An applied migration must not change |
| `backup` | Backs up the database to the `clickhouse-backups` volume |
| `registry load` | Copies the projects into ClickHouse for Grafana |
| `keys add`, `keys disable` | Issue and disable keys in the registry |
| `geo update` | Installs this month's DB-IP Lite databases (CC BY 4.0); ingest picks them up within a minute |
| `replay` | Stores uploads kept as `schema_newer` once the backend knows their schema |
| `schema check` | Compares the schema copy with its `SHA256SUMS` |
| `dashboards import` | Creates the dashboards of `grafana/dashboards/` that Grafana lacks |
| `dashboards export` | Prints every dashboard as one JSON object |

Migrations only add: `CREATE … IF NOT EXISTS`, `ADD COLUMN`, `MODIFY TTL`.

## Keys

```bash
docker compose run --rm tools beacon keys add --project acme --name "Acme" --package com.acme.app
docker compose run --rm tools beacon keys disable <key>
```

`add` creates the project if it is new and prints the beacon URL; `--package '*'` accepts any app. Ingest reads the registry at start: `docker compose restart ingest` applies the change, and `registry load` shows it in Grafana.

## Grafana

Dashboards are edited and saved in Grafana. To keep the changes in git:

```bash
docker compose run --rm -T tools beacon dashboards export | uv run beacon dashboards save grafana/dashboards
```

It replaces `grafana/dashboards/` with what Grafana has. `dashboards import` never overwrites a dashboard Grafana already has.

## Checks

```bash
uv run ruff format --check . && uv run ruff check . && uv run mypy src scripts/load_test.py && uv run pytest
```

On a Docker host, `scripts/integration.sh` runs the integration tests against a throwaway ClickHouse.

## License

[MIT](LICENSE)
