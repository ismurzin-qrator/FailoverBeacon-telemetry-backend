import gzip
import http.client
import logging
import os
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import maxminddb

log = logging.getLogger(__name__)

HOST = "download.db-ip.com"
PATH = "/free/"
FILE = "dbip-{kind}-lite-{month}.mmdb.gz"
USER_AGENT = "beacon-geo-update"
KINDS = ("country", "asn")
CHECK_INTERVAL = 60.0


@dataclass(frozen=True)
class Location:
    country: str = ""
    asn: int | None = None
    as_org: str = ""
    database_date: date | None = None


class Geo:
    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._readers: dict[str, Any] = {}
        self._mtimes: dict[str, float] = {}
        self._checked = 0.0

    def locate(self, ip: str | None) -> Location:
        self._reload_if_changed()
        country, asn = self._readers.get("country"), self._readers.get("asn")
        if not ip or country is None:
            return Location()
        try:
            place = country.get(ip) or {}
            network = (asn.get(ip) if asn else None) or {}
        except ValueError:
            return Location()
        return Location(
            country=place.get("country", {}).get("iso_code", ""),
            asn=network.get("autonomous_system_number"),
            as_org=network.get("autonomous_system_organization", ""),
            database_date=datetime.fromtimestamp(country.metadata().build_epoch, UTC).date(),
        )

    def _reload_if_changed(self) -> None:
        if time.monotonic() - self._checked < CHECK_INTERVAL and self._checked:
            return
        self._checked = time.monotonic()
        for kind in KINDS:
            path = self._directory / f"{kind}.mmdb"
            try:
                mtime = path.stat().st_mtime
            except FileNotFoundError:
                continue
            if self._mtimes.get(kind) != mtime:
                self._readers[kind] = maxminddb.open_database(str(path))
                self._mtimes[kind] = mtime
                log.info("opened %s database", kind)


def update(directory: Path, today: date) -> list[str]:
    # Last month's files serve until DB-IP publishes this month's.
    months = [today.strftime("%Y-%m")]
    previous = date(today.year - (today.month == 1), (today.month - 2) % 12 + 1, 1)
    months.append(previous.strftime("%Y-%m"))
    done = []
    for kind in KINDS:
        for month in months:
            name = FILE.format(kind=kind, month=month)
            data = _download(f"{PATH}{name}")
            if data is None:
                continue
            temporary = directory / f".{kind}.mmdb.new"
            temporary.write_bytes(gzip.decompress(data))
            maxminddb.open_database(str(temporary)).close()  # refuses a broken file
            os.replace(temporary, directory / f"{kind}.mmdb")
            done.append(name)
            break
        else:
            raise RuntimeError(f"no {kind} database for {', '.join(months)}")
    return done


def _download(path: str) -> bytes | None:
    connection = http.client.HTTPSConnection(HOST, timeout=120)
    try:
        # DB-IP answers 403 to urllib's default User-Agent.
        connection.request("GET", path, headers={"User-Agent": USER_AGENT})
        response = connection.getresponse()
        if response.status == 404:
            return None
        if response.status != 200:
            raise OSError(f"{HOST}{path}: HTTP {response.status}")
        return response.read()
    finally:
        connection.close()
