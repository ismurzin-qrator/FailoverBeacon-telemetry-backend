import gzip
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from beacon.geo import Location
from beacon.registry import Registry
from beacon.schema import COPY
from beacon.upload import Context

SAMPLES = COPY / "samples/v1"
SHOP_KEY = "a" * 26
COMMUNITY_KEY = "b" * 26
RECEIVED_AT = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def samples(schema: str, kind: str) -> dict[str, Any]:
    return {
        p.stem: json.loads(p.read_text()) for p in sorted((SAMPLES / schema / kind).glob("*.json"))
    }


def metadata(**changes: Any) -> dict[str, Any]:
    return {**samples("upload-metadata", "valid")["full"], **changes}


def lines(meta: dict[str, Any], events: list[Any]) -> list[bytes]:
    return [json.dumps(x).encode() for x in [meta, *events]]


def body(meta: dict[str, Any], events: list[Any], compress: bool = True) -> bytes:
    data = b"\n".join(lines(meta, events)) + b"\n"
    return gzip.compress(data) if compress else data


def context() -> Context:
    return Context(
        project_id="example-shop",
        upload_id=UUID("01a1d550-4cad-7e1b-8145-0df2583c8df3"),
        received_at=RECEIVED_AT,
        location=Location("NL", 1136, "KPN B.V.", None),
    )


def registry() -> Registry:
    return Registry.load(Path(__file__).parent.parent / "registry.example.json")


class FakeClient:
    def __init__(self, fail: bool = False, stored_bytes: int = 0) -> None:
        self.inserts: dict[str, list[list[Any]]] = {}
        self.columns: dict[str, list[str]] = {}
        self.fail = fail
        self.stored_bytes = stored_bytes

    async def insert(
        self, table: str, rows: list[list[Any]], column_names: list[str], settings: Any
    ) -> None:
        if self.fail:
            from clickhouse_connect.driver.exceptions import OperationalError

            raise OperationalError("down")
        assert settings == {"async_insert": 1, "wait_for_async_insert": 1}
        self.inserts.setdefault(table, []).extend(rows)
        self.columns[table] = column_names

    async def query(self, sql: str, parameters: Any = None) -> Any:
        return type("Result", (), {"first_row": (self.stored_bytes,)})()

    async def ping(self) -> bool:
        return not self.fail

    def rows(self, table: str) -> list[dict[str, Any]]:
        return [
            dict(zip(self.columns[table], row, strict=True)) for row in self.inserts.get(table, [])
        ]


class FakeClickHouse:
    def __init__(self, client: FakeClient) -> None:
        self._client = client

    async def client(self) -> FakeClient:
        if self._client.fail:
            from clickhouse_connect.driver.exceptions import OperationalError

            raise OperationalError("down")
        return self._client

    async def available(self) -> bool:
        return not self._client.fail

    async def close(self) -> None:
        pass
