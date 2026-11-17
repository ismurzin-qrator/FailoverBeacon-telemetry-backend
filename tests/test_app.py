import gzip
import os

import pytest

from beacon import app as beacon_app
from beacon.app import build_app
from beacon.geo import Geo
from tests.helpers import SHOP_KEY, FakeClickHouse, FakeClient, body, metadata, registry, samples

EVENTS = list(samples("event", "valid").values())
URL = f"/v1/beacon/{SHOP_KEY}"
NDJSON = {"Content-Type": "application/x-ndjson"}
GZIP = {**NDJSON, "Content-Encoding": "gzip"}


@pytest.fixture
def clickhouse():
    return FakeClient()


async def start(aiohttp_server, aiohttp_client, clickhouse, tmp_path):
    app = build_app(registry(), FakeClickHouse(clickhouse), Geo(tmp_path))
    # As in production: the body reaches the handler as sent, still compressed.
    return await aiohttp_client(await aiohttp_server(app, auto_decompress=False))


@pytest.fixture
async def client(aiohttp_server, aiohttp_client, clickhouse, tmp_path):
    return await start(aiohttp_server, aiohttp_client, clickhouse, tmp_path)


async def test_an_upload_is_stored_events_first_then_its_row(client, clickhouse):
    response = await client.post(URL, data=body(metadata(), EVENTS), headers=GZIP)

    assert response.status == 200
    assert list(clickhouse.inserts) == ["events", "uploads"]
    upload_row = clickhouse.rows("uploads")[0]
    assert (upload_row["status"], upload_row["events"], upload_row["rejected"]) == (
        "stored",
        len(EVENTS),
        0,
    )
    assert {r["project_id"] for r in clickhouse.rows("events")} == {"example-shop"}


async def test_a_body_without_gzip_is_read_as_is(client, clickhouse):
    response = await client.post(
        URL, data=body(metadata(), EVENTS[:1], compress=False), headers=NDJSON
    )

    assert response.status == 200 and len(clickhouse.inserts["events"]) == 1


async def test_a_body_over_1_mib_once_decompressed_is_accepted(client, clickhouse):
    # aiohttp's own decompression would refuse it at client_max_size, 1 MiB.
    padded = [dict(EVENTS[0], host="h" * 200) for _ in range(2000)]
    data = body(metadata(), padded)
    assert 1024 * 1024 < len(gzip.decompress(data)) < 4 * 1024 * 1024

    response = await client.post(URL, data=data, headers=GZIP)

    assert response.status == 200 and len(clickhouse.inserts["events"]) == 2000


async def test_a_body_over_4_mib_once_decompressed_is_refused(client, clickhouse):
    padded = [dict(EVENTS[0], host="h" * 200) for _ in range(6000)]

    response = await client.post(URL, data=body(metadata(), padded), headers=GZIP)

    assert response.status == 413 and "events" not in clickhouse.inserts


@pytest.mark.parametrize(
    ("key", "data", "headers", "status", "recorded"),
    [
        ("z" * 26, None, GZIP, 410, None),
        (SHOP_KEY, None, {**GZIP, "Content-Type": "text/plain"}, 415, "unsupported_type"),
        (SHOP_KEY, None, {**GZIP, "Content-Encoding": "br"}, 415, "unsupported_encoding"),
        (SHOP_KEY, b"\x1f\x8b broken", GZIP, 400, "bad_body"),
        (SHOP_KEY, os.urandom(1100 * 1024), GZIP, 413, "too_large"),
        (SHOP_KEY, gzip.compress(b"0" * (5 * 1024 * 1024)), GZIP, 413, "too_large"),
        (
            SHOP_KEY,
            body(metadata(app_package="com.other.app"), EVENTS[:1]),
            GZIP,
            403,
            "package_mismatch",
        ),
        (SHOP_KEY, body(metadata(os_api_level=21), EVENTS[:1]), GZIP, 400, "bad_metadata"),
    ],
)
async def test_refused_uploads(client, clickhouse, key, data, headers, status, recorded):
    response = await client.post(
        f"/v1/beacon/{key}", data=data or body(metadata(), EVENTS[:1]), headers=headers
    )

    assert response.status == status
    assert "events" not in clickhouse.inserts
    statuses = (
        [r["status"] for r in clickhouse.rows("uploads")] if "uploads" in clickhouse.inserts else []
    )
    assert statuses == ([recorded] if recorded else [])


async def test_a_newer_schema_is_kept_whole_for_the_replay(client, clickhouse):
    meta = metadata(schema_version="1.1.0")
    response = await client.post(URL, data=body(meta, [dict(EVENTS[0], new_field=1)]), headers=GZIP)

    assert response.status == 200
    assert [r["reason"] for r in clickhouse.rows("rejected_events")] == ["schema_newer"] * 2
    assert clickhouse.rows("uploads")[0]["status"] == "schema_newer"


async def test_a_newer_schema_over_the_budget_is_only_counted(
    aiohttp_server, aiohttp_client, tmp_path
):
    clickhouse = FakeClient(stored_bytes=beacon_app.SCHEMA_NEWER_BUDGET)
    client = await start(aiohttp_server, aiohttp_client, clickhouse, tmp_path)

    response = await client.post(
        URL, data=body(metadata(schema_version="1.1.0"), EVENTS[:1]), headers=GZIP
    )

    assert response.status == 200
    assert "rejected_events" not in clickhouse.inserts
    assert clickhouse.rows("uploads")[0]["status"] == "schema_newer_over_budget"


async def test_clickhouse_down_answers_503(aiohttp_server, aiohttp_client, tmp_path):
    client = await start(aiohttp_server, aiohttp_client, FakeClient(fail=True), tmp_path)

    assert (await client.post(URL, data=body(metadata(), EVENTS[:1]), headers=GZIP)).status == 503
    assert (await client.get("/v1/health")).status == 503
    assert (await client.get("/healthz")).status == 503


async def test_inserts_over_the_bound_answer_503(client):
    semaphore = client.app[beacon_app.INSERTS]
    for _ in range(beacon_app.CONCURRENT_INSERTS):
        await semaphore.acquire()

    assert (await client.post(URL, data=body(metadata(), EVENTS[:1]), headers=GZIP)).status == 503


async def test_health(client):
    public = await client.get("/v1/health")
    internal = await client.get("/healthz")

    assert public.status == 200 and await public.read() == b""
    assert internal.status == 200 and await internal.json() == {"clickhouse": True, "projects": 2}
