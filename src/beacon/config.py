import os
from dataclasses import dataclass
from pathlib import Path

DATABASE = os.environ.get("CLICKHOUSE_DATABASE", "telemetry")


@dataclass(frozen=True)
class Settings:
    registry: Path
    clickhouse_host: str
    clickhouse_port: int
    clickhouse_user: str
    clickhouse_password: str
    geo_dir: Path

    @staticmethod
    def from_env() -> "Settings":
        return Settings(
            registry=Path(_required("BEACON_REGISTRY")),
            geo_dir=Path(os.environ.get("BEACON_GEO_DIR", "/var/lib/beacon/geo")),
            clickhouse_host=os.environ.get("CLICKHOUSE_HOST", "clickhouse"),
            clickhouse_port=int(os.environ.get("CLICKHOUSE_PORT", "8123")),
            clickhouse_user=os.environ.get("CLICKHOUSE_USER", "ingest"),
            clickhouse_password=_required("CLICKHOUSE_PASSWORD"),
        )


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value
