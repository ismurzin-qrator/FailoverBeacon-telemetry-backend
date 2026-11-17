import os
from pathlib import Path

import pytest

from beacon import config, mapping, replay, storage, upload
from beacon.app import build_app
from beacon.clickhouse import ClickHouse
from beacon.geo import Geo
from tests.helpers import SHOP_KEY, body, metadata, registry, samples

pytestmark = pytest.mark.skipif(
    "CLICKHOUSE_TEST_HOST" not in os.environ, reason="needs scripts/integration.sh"
)
VALID = list(samples("event", "valid").values())
INVALID = list(samples("event", "invalid").values())
GZIP = {"Content-Type": "application/x-ndjson", "Content-Encoding": "gzip"}


@pytest.fixture(scope="module")
def settings():
    assert config.DATABASE.startswith("it_"), "integration tests never touch a real database"
    os.environ.update(
        BEACON_REGISTRY="unused", CLICKHOUSE_PASSWORD=os.environ["CLICKHOUSE_TEST_PASSWORD"]
    )
    return config.Settings(
        registry=Path("unused"),
        clickhouse_host=os.environ["CLICKHOUSE_TEST_HOST"],
        clickhouse_port=8123,
        clickhouse_user="default",
        clickhouse_password=os.environ["CLICKHOUSE_TEST_PASSWORD"],
        geo_dir=Path("/nonexistent"),
    )


@pytest.fixture(scope="module")
def sync(settings):
    client = storage.connect(settings)
    assert storage.migrate(client) == sorted(
        p.name for p in (Path(storage.__file__).parent / "migrations").glob("*.sql")
    )
    yield client
    client.command(f"DROP DATABASE {config.DATABASE}")


@pytest.fixture
async def client(aiohttp_server, aiohttp_client, settings, sync):
    app = build_app(registry(), ClickHouse(settings), Geo(settings.geo_dir))
    return await aiohttp_client(await aiohttp_server(app, auto_decompress=False))


def count(sync, sql):
    return sync.query(sql).first_row[0]


def test_every_schema_field_has_a_column_and_a_second_migrate_does_nothing(sync):
    assert mapping.check(sync) == []
    assert storage.migrate(sync) == []


def test_a_changed_applied_migration_stops_the_runner(sync):
    def record(digest):
        sync.command("DELETE FROM schema_migrations WHERE name = '0001_events.sql'")
        sync.insert(
            "schema_migrations", [["0001_events.sql", digest]], column_names=["name", "sha256"]
        )

    original = count(sync, "SELECT sha256 FROM schema_migrations WHERE name = '0001_events.sql'")
    record("0" * 64)
    try:
        with pytest.raises(storage.MigrationError, match="changed"):
            storage.migrate(sync)
    finally:
        record(original)
    assert storage.migrate(sync) == []


async def test_every_valid_sample_is_stored_and_every_invalid_one_rejected(client, sync):
    response = await client.post(
        f"/v1/beacon/{SHOP_KEY}", data=body(metadata(), VALID + INVALID), headers=GZIP
    )
    assert response.status == 200

    assert count(sync, "SELECT count() FROM events") == len(VALID)
    assert count(sync, "SELECT count() FROM rejected_events WHERE reason = 'invalid'") == len(
        INVALID
    )
    assert sync.query("SELECT status, events, rejected FROM uploads").result_rows == [
        ("stored", len(VALID), len(INVALID))
    ]
    assert (
        count(sync, "SELECT countIf(abs(weight * sampling_fraction - 1) > 1e-9) FROM events") == 0
    )


async def test_a_resent_upload_merges_into_one_copy(client, sync):
    before = count(sync, "SELECT count() FROM events FINAL")
    for _ in range(2):
        await client.post(f"/v1/beacon/{SHOP_KEY}", data=body(metadata(), VALID[:5]), headers=GZIP)

    assert count(sync, "SELECT count() FROM events FINAL") - before <= 5


async def test_a_newer_schema_is_kept_then_replayed(client, sync, settings, monkeypatch):
    meta = metadata(schema_version="1.1.0")
    event = dict(VALID[0], call_id="00000000000abcde")
    response = await client.post(f"/v1/beacon/{SHOP_KEY}", data=body(meta, [event]), headers=GZIP)
    assert response.status == 200
    assert count(sync, "SELECT count() FROM rejected_events WHERE reason = 'schema_newer'") == 2

    monkeypatch.setattr(upload, "BACKEND_VERSION", (1, 1, 0))
    clickhouse = ClickHouse(settings)
    report = await replay.replay(await clickhouse.client())
    assert len(report) == 1 and "1 events" in report[0]
    assert await replay.replay(await clickhouse.client()) == []
    await clickhouse.close()

    assert count(sync, "SELECT count() FROM events WHERE call_id = 0xabcde") == 1
    assert count(sync, "SELECT count() FROM uploads WHERE status = 'replayed'") == 1


def test_the_registry_copy_holds_the_projects(sync):
    storage.copy_registry(sync, registry())

    projects = sync.query("SELECT project_id, any_package FROM projects ORDER BY project_id")
    assert projects.result_rows == [("community", True), ("example-shop", False)]


def test_a_backup_lands_on_the_backups_disk(sync):
    name = storage.backup(sync)

    created = sync.query(
        "SELECT count() FROM system.backups"
        " WHERE status = 'BACKUP_CREATED' AND name LIKE {n:String}",
        parameters={"n": f"%{name}%"},
    )
    assert created.first_row[0] == 1
