import asyncio
import logging
from typing import Any

from aiohttp import web
from aiohttp.streams import StreamReader
from clickhouse_connect.driver.exceptions import ClickHouseError

from beacon import enrich, upload
from beacon.clickhouse import ClickHouse
from beacon.config import Settings
from beacon.geo import Geo
from beacon.registry import Project, Registry
from beacon.upload import BadBody, Context, Result, TooLarge

log = logging.getLogger(__name__)

CLICKHOUSE = web.AppKey("clickhouse", ClickHouse)
REGISTRY = web.AppKey("registry", Registry)
GEO = web.AppKey("geo", Geo)
INSERTS = web.AppKey("inserts", asyncio.Semaphore)

DEADLINE = 20.0  # below the 30 s the schema gives clients to wait
CONCURRENT_INSERTS = 64
ASYNC_INSERT = {"async_insert": 1, "wait_for_async_insert": 1}
SCHEMA_NEWER_BUDGET = 100 * 1024 * 1024  # bytes per project per day


async def create_app() -> web.AppRunner:
    settings = Settings.from_env()
    app = build_app(Registry.load(settings.registry), ClickHouse(settings), Geo(settings.geo_dir))
    # aiohttp would decompress up to client_max_size and refuse valid uploads; ingest caps both
    # sizes itself. The access log would hold client addresses and keys.
    return web.AppRunner(app, auto_decompress=False, access_log=None)


def build_app(registry: Registry, clickhouse: ClickHouse, geo: Geo) -> web.Application:
    app = web.Application()
    app[REGISTRY] = registry
    app[CLICKHOUSE] = clickhouse
    app[GEO] = geo
    app[INSERTS] = asyncio.Semaphore(CONCURRENT_INSERTS)
    app.on_cleanup.append(_close)
    app.router.add_post("/v1/beacon/{key}", beacon)
    app.router.add_get("/healthz", internal_health)
    app.router.add_get("/v1/health", public_health)
    return app


async def beacon(request: web.Request) -> web.Response:
    app = request.app
    project = app[REGISTRY].project_for_key(request.match_info["key"])
    if project is None:
        return web.Response(status=410)
    if app[INSERTS].locked():
        return web.Response(status=503)
    ctx = Context(
        project_id=project.id,
        upload_id=enrich.uuid7(),
        received_at=enrich.now(),
        location=app[GEO].locate(request.headers.get("X-Real-IP")),
    )
    try:
        async with asyncio.timeout(DEADLINE):
            result = await _read(request, project, ctx)
            async with app[INSERTS]:
                await write(await app[CLICKHOUSE].client(), ctx, result)
    except (TimeoutError, ClickHouseError, OSError) as e:
        log.warning("upload not stored: %s", type(e).__name__)
        return web.Response(status=503)
    return web.Response(status=result.code)


async def _read(request: web.Request, project: Project, ctx: Context) -> Result:
    if request.content_type != "application/x-ndjson":
        return Result(status="unsupported_type", code=415)
    encoding = request.headers.get("Content-Encoding", "identity").lower()
    if encoding not in ("gzip", "identity"):
        return Result(status="unsupported_encoding", code=415)
    try:
        body = await _read_capped(request.content)
    except TooLarge:
        return Result(status="too_large", code=413)
    result = Result(status="", code=0, body_bytes=len(body))
    try:
        data = upload.gunzip(body) if encoding == "gzip" else body
        result.decompressed_bytes = len(data)
        lines = upload.split_lines(data)
        metadata = upload.read_header(lines[0])
    except TooLarge:
        return _status(result, "too_large", 413)
    except BadBody:
        return _status(result, "bad_body", 400)
    result.metadata = metadata
    if not project.accepts_package(metadata["app_package"]):
        return _status(result, "package_mismatch", 403)
    if upload.is_newer(metadata["schema_version"]):
        result.rejected = [
            upload.rejected_row(ctx, number, "schema_newer", "", line.decode("utf-8", "replace"))
            for number, line in enumerate(lines, start=1)
        ]
        result.rejected_count = len(lines)
        return _status(result, "schema_newer", 200)
    try:
        processed = upload.process(lines, metadata, ctx)
    except BadBody:
        return _status(result, "bad_metadata", 400)
    processed.body_bytes, processed.decompressed_bytes = len(body), len(data)
    return processed


def _status(result: Result, status: str, code: int) -> Result:
    result.status, result.code = status, code
    return result


async def _read_capped(stream: StreamReader) -> bytes:
    chunks, size = [], 0
    async for chunk in stream.iter_any():
        size += len(chunk)
        if size > upload.COMPRESSED_CAP:
            raise TooLarge("body over the cap")
        chunks.append(chunk)
    return b"".join(chunks)


async def write(client: Any, ctx: Context, result: Result) -> None:
    # The upload row goes last, so its presence means the events are stored.
    if result.status == "schema_newer" and await _over_budget(client, ctx.project_id):
        result.status, result.rejected = "schema_newer_over_budget", []
    if result.events:
        await client.insert(
            "events", result.events, column_names=upload.EVENT_INSERT_COLUMNS, settings=ASYNC_INSERT
        )
    if result.rejected:
        await client.insert(
            "rejected_events",
            result.rejected,
            column_names=upload.REJECTED_INSERT_COLUMNS,
            settings=ASYNC_INSERT,
        )
    await client.insert(
        "uploads",
        [upload.upload_row(ctx, result)],
        column_names=upload.UPLOAD_INSERT_COLUMNS,
        settings=ASYNC_INSERT,
    )


async def _over_budget(client: Any, project_id: str) -> bool:
    stored = await client.query(
        "SELECT sum(decompressed_bytes) FROM uploads WHERE project_id = {project:String}"
        " AND status = 'schema_newer' AND received_at >= toStartOfDay(now64(3, 'UTC'))",
        parameters={"project": project_id},
    )
    return bool(stored.first_row[0] >= SCHEMA_NEWER_BUDGET)


async def internal_health(request: web.Request) -> web.Response:
    clickhouse = await request.app[CLICKHOUSE].available()
    return web.json_response(
        {"clickhouse": clickhouse, "projects": len(request.app[REGISTRY].projects)},
        status=200 if clickhouse else 503,
    )


async def public_health(request: web.Request) -> web.Response:
    return web.Response(status=200 if await request.app[CLICKHOUSE].available() else 503)


async def _close(app: web.Application) -> None:
    await app[CLICKHOUSE].close()
