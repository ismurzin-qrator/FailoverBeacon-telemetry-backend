import csv
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

MAX_AGE = timedelta(hours=24)
INT64_MAX = 2**63 - 1

# ITU E.212 as of 2017-02-01, from AOSP MccTable.java. Shared codes such as 901 have no country.
_MCC = {
    row["mcc"]: row["country"] for row in csv.DictReader((Path(__file__).parent / "mcc.csv").open())
}


def uuid7() -> uuid.UUID:
    # RFC 9562 version 7: 48 bits of Unix milliseconds, then random bits.
    millis = time.time_ns() // 1_000_000
    rand = secrets.randbits(74)
    value = (millis & (2**48 - 1)) << 80 | 0x7 << 76 | (rand >> 62) << 64 | 0b10 << 62
    return uuid.UUID(int=value | rand & (2**62 - 1))


def now() -> datetime:
    return datetime.now(UTC)


def event_time(received_at: datetime, sent_at: int, start_time: int) -> tuple[datetime, bool]:
    # Both client times come from one clock; the clamp bounds a clock changed between processes.
    age = sent_at - start_time
    if age < 0:
        return received_at, True
    if age > MAX_AGE // timedelta(milliseconds=1):
        return received_at - MAX_AGE, True
    return received_at - timedelta(milliseconds=age), False


def delivery_delay(sent_at: int, start_time: int) -> int:
    return max(-INT64_MAX, min(sent_at - start_time, INT64_MAX))


def sim_country(mcc: str | None) -> str:
    return _MCC.get(mcc or "", "")
