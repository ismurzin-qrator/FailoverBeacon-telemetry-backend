"""Schema fields to ClickHouse columns.

A column is named by the field's JSON path joined with "_"; an array of objects is a Nested column
("attempts.ip"). An integer is UInt16 when its bounds fit, Int64 when it may be negative, UInt64
otherwise. An optional number or boolean is Nullable, so a missing value never reads as 0 or false.
"""

import json
from typing import Any

from clickhouse_connect.driver.client import Client

from beacon.config import DATABASE
from beacon.schema import COPY

# Columns that come from ingest or ClickHouse.
ADDED = {
    "events": {
        "project_id", "upload_id", "received_at", "event_time", "event_time_clamped",
        "delivery_delay", "ip_country", "asn", "as_org", "sim_country", "sdk_version",
        "app_package", "app_version", "os_api_level", "weight", "wq", "outcome",
    },
    "uploads": {
        "project_id", "upload_id", "received_at", "status", "response_code", "events", "rejected",
        "body_bytes", "decompressed_bytes", "ip_country", "asn", "as_org", "geo_database_date",
    },
}  # fmt: skip
# call_id arrives as 16 hex digits.
CONVERTED = {"events": {"call_id": {"UInt64"}}}
TABLES = {"events": "event", "uploads": "upload-metadata"}
UINT16_MAX = 65535


def expected_columns(schema_name: str) -> dict[str, set[str]]:
    schema = json.loads((COPY / f"schemas/v1/{schema_name}.schema.json").read_text())
    columns: dict[str, set[str]] = {}
    _walk(schema, schema, "", "_", False, columns)
    return columns


def _walk(
    root: dict[str, Any],
    node: dict[str, Any],
    prefix: str,
    separator: str,
    in_array: bool,
    columns: dict[str, set[str]],
) -> None:
    required = set(node.get("required", []))
    for key, sub in node.get("properties", {}).items():
        sub = _resolve(root, sub)
        name = f"{prefix}{key}"
        if sub.get("type") == "object":
            _walk(root, sub, f"{name}_", "_", in_array, columns)
        elif sub.get("type") == "array" and _resolve(root, sub["items"]).get("type") == "object":
            _walk(root, _resolve(root, sub["items"]), f"{name}.", ".", True, columns)
        else:
            types = _types(root, sub, optional=key not in required)
            columns[name] = {f"Array({t})" for t in types} if in_array else types


def _types(root: dict[str, Any], node: dict[str, Any], optional: bool) -> set[str]:
    if node.get("type") == "array":
        return {f"Array({t})" for t in _types(root, _resolve(root, node["items"]), False)}
    if "enum" in node or node.get("type") == "string":
        return {"String", "LowCardinality(String)"}
    if isinstance(node.get("const"), int):
        return {"UInt8"}
    kind = {"integer": _integer(node), "number": "Float64", "boolean": "Bool"}[node["type"]]
    return {f"Nullable({kind})" if optional else kind}


def _integer(node: dict[str, Any]) -> str:
    if node.get("minimum", -1) < 0:
        return "Int64"
    return "UInt16" if node.get("maximum", UINT16_MAX + 1) <= UINT16_MAX else "UInt64"


def _resolve(root: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        target: Any = root
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        node = target
    return node


def check(client: Client) -> list[str]:
    problems = []
    for table, schema_name in TABLES.items():
        rows = client.query(
            "SELECT name, type FROM system.columns WHERE database = {db:String}"
            " AND table = {table:String}",
            parameters={"db": DATABASE, "table": table},
        ).result_rows
        actual: dict[str, str] = {name: kind for name, kind in rows}
        expected = expected_columns(schema_name) | CONVERTED.get(table, {})
        for name, types in sorted(expected.items()):
            if name not in actual:
                problems.append(f"{table}: no column for {schema_name} field {name}")
            elif actual[name] not in types:
                problems.append(f"{table}.{name}: {actual[name]}, expected {' or '.join(types)}")
        for name in sorted(set(actual) - set(expected) - ADDED[table]):
            problems.append(f"{table}.{name}: in no schema and not added by ingest")
    return problems
