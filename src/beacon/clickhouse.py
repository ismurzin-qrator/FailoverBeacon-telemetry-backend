import asyncio
import logging

import clickhouse_connect
from clickhouse_connect.driver.asyncclient import AsyncClient

from beacon.config import DATABASE, Settings

log = logging.getLogger(__name__)


class ClickHouse:
    # Created on first use: creating a client queries the server, and ingest must start and
    # answer 503 while ClickHouse is down.

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def client(self) -> AsyncClient:
        async with self._lock:
            if self._client is None:
                self._client = await clickhouse_connect.get_async_client(
                    host=self._settings.clickhouse_host,
                    port=self._settings.clickhouse_port,
                    username=self._settings.clickhouse_user,
                    password=self._settings.clickhouse_password,
                    database=DATABASE,
                    connect_timeout=5,
                )
            return self._client

    async def available(self) -> bool:
        try:
            client = await self.client()
            return bool(await client.ping())
        except Exception as e:
            log.warning("ClickHouse unavailable: %s", type(e).__name__)
            return False

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
