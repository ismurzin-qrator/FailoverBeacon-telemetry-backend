import json
import re
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

import jsonschema_rs

from beacon import enrich
from beacon.geo import Location
from beacon.rows import EVENT_COLUMNS, METADATA_COLUMNS, UnstorableValue, flatten
from beacon.schema import COPY

COMPRESSED_CAP = 1024 * 1024
DECOMPRESSED_CAP = 4 * 1024 * 1024
MAX_LINES = 20_000
STORED_INVALID_LINES = 100
STORED_LINE_BYTES = 4096
VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")

BACKEND_VERSION = tuple(int(p) for p in (COPY / "VERSION").read_text().split("."))


def _validator(path: str) -> Any:
    return jsonschema_rs.validator_for(json.loads((COPY / path).read_text()))


METADATA = _validator("schemas/v1/upload-metadata.schema.json")
EVENTS = {1: _validator("schemas/v1/event.schema.json")}

ADDED_EVENT_COLUMNS = (
    "project_id", "upload_id", "received_at", "event_time", "event_time_clamped",
    "delivery_delay", "ip_country", "asn", "as_org", "sim_country", "sdk_version",
    "app_package", "app_version", "os_api_level",
)  # fmt: skip
EVENT_INSERT_COLUMNS = [*ADDED_EVENT_COLUMNS, *EVENT_COLUMNS]
UPLOAD_INSERT_COLUMNS = [
    "project_id", "upload_id", "received_at", "status", "response_code", "events", "rejected",
    "body_bytes", "decompressed_bytes", "ip_country", "asn", "as_org", "geo_database_date",
    *METADATA_COLUMNS,
]  # fmt: skip
REJECTED_INSERT_COLUMNS = [
    "project_id", "upload_id", "received_at", "line", "reason", "error", "raw",
]  # fmt: skip


class BadBody(Exception):
    pass


class TooLarge(Exception):
    pass


@dataclass(frozen=True)
class Context:
    project_id: str
    upload_id: UUID
    received_at: datetime
    location: Location


@dataclass
class Result:
    status: str
    code: int
    metadata: dict[str, Any] = field(default_factory=dict)
    events: list[list[Any]] = field(default_factory=list)
    rejected: list[list[Any]] = field(default_factory=list)
    rejected_count: int = 0
    body_bytes: int = 0
    decompressed_bytes: int = 0


def gunzip(data: bytes) -> bytes:
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = decompressor.decompress(data, DECOMPRESSED_CAP + 1)
        if len(out) > DECOMPRESSED_CAP or decompressor.unconsumed_tail:
            raise TooLarge("decompressed body over the cap")
        out += decompressor.flush()
    except zlib.error as e:
        raise BadBody(f"bad gzip: {e}") from e
    if not decompressor.eof or decompressor.unused_data:
        raise BadBody("bad gzip: truncated or followed by other data")
    if len(out) > DECOMPRESSED_CAP:
        raise TooLarge("decompressed body over the cap")
    return out


def split_lines(body: bytes) -> list[bytes]:
    lines = body.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    if len(lines) > MAX_LINES:
        raise TooLarge(f"more than {MAX_LINES} lines")
    if not lines:
        raise BadBody("empty body")
    return lines


def read_header(line: bytes) -> dict[str, Any]:
    try:
        metadata = json.loads(line)
    except ValueError as e:
        raise BadBody(f"metadata line is not JSON: {e}") from e
    if not isinstance(metadata, dict):
        raise BadBody("metadata line is not an object")
    if not isinstance(metadata.get("schema_version"), str) or not VERSION.match(
        metadata["schema_version"]
    ):
        raise BadBody("metadata line has no schema_version")
    if not isinstance(metadata.get("app_package"), str):
        raise BadBody("metadata line has no app_package")
    return metadata


def is_newer(schema_version: str) -> bool:
    # Patch versions change no field.
    major, minor, _ = (int(p) for p in schema_version.split("."))
    return (major, minor) > BACKEND_VERSION[:2]


def first_error(validator: Any, instance: Any) -> str | None:
    error = next(validator.iter_errors(instance), None)
    if error is None:
        return None
    return f"/{'/'.join(map(str, error.instance_path))}: {error.message}"[:STORED_LINE_BYTES]


def process(lines: list[bytes], metadata: dict[str, Any], ctx: Context) -> Result:
    if error := first_error(METADATA, metadata):
        raise BadBody(f"metadata line: {error}")
    result = Result(status="stored", code=200, metadata=metadata)
    for number, line in enumerate(lines[1:], start=2):
        row, error = _event_row(line, metadata, ctx)
        if row is not None:
            result.events.append(row)
            continue
        result.rejected_count += 1
        if len(result.rejected) < STORED_INVALID_LINES:
            raw = line[:STORED_LINE_BYTES].decode("utf-8", errors="replace")
            result.rejected.append(rejected_row(ctx, number, "invalid", error or "", raw))
    return result


def _event_row(
    line: bytes, metadata: dict[str, Any], ctx: Context
) -> tuple[list[Any] | None, str | None]:
    try:
        event = json.loads(line)
    except ValueError as e:
        return None, f"not JSON: {e}"
    if not isinstance(event, dict):
        return None, "not an object"
    version = event.get("format_version")
    validator = EVENTS.get(version) if type(version) is int else None
    if validator is None:
        return None, f"unknown format_version {event.get('format_version')!r}"
    if error := first_error(validator, event):
        return None, error
    sent_at, start = metadata["sent_at"], event["start_time"]
    time, clamped = enrich.event_time(ctx.received_at, sent_at, start)
    event["call_id"] = int(event["call_id"], 16)
    try:
        values = flatten(event, EVENT_COLUMNS)
    except UnstorableValue as e:
        return None, str(e)
    loc = ctx.location
    added = [
        ctx.project_id, ctx.upload_id, ctx.received_at, time, clamped,
        enrich.delivery_delay(sent_at, start), loc.country, loc.asn, loc.as_org,
        enrich.sim_country(event.get("network", {}).get("carrier", {}).get("mcc")),
        metadata["sdk_version"], metadata["app_package"], metadata.get("app_version", ""),
        metadata["os_api_level"],
    ]  # fmt: skip
    return added + values, None


def rejected_row(ctx: Context, line: int, reason: str, error: str, raw: str) -> list[Any]:
    return [ctx.project_id, ctx.upload_id, ctx.received_at, line, reason, error, raw]


def upload_row(ctx: Context, result: Result) -> list[Any]:
    loc = ctx.location
    if result.status in ("stored", "replayed"):
        metadata = flatten(result.metadata, METADATA_COLUMNS)
    else:
        metadata = _unvalidated(result.metadata)
    return [
        ctx.project_id, ctx.upload_id, ctx.received_at, result.status, result.code,
        len(result.events), result.rejected_count, result.body_bytes, result.decompressed_bytes,
        loc.country, loc.asn, loc.as_org, loc.database_date, *metadata,
    ]  # fmt: skip


def _unvalidated(metadata: dict[str, Any]) -> list[Any]:
    # A refused or newer upload still records its package and version where they fit the column.
    values: list[Any] = []
    for name, kind in METADATA_COLUMNS.items():
        value = metadata.get(name)
        if "String" in kind:
            values.append(value if isinstance(value, str) else "")
        elif type(value) is int and 0 <= value <= 2**64 - 1:
            values.append(value)
        else:
            values.append(None if kind.startswith("Nullable(") else 0)
    return values
