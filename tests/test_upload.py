import gzip
import json
from datetime import timedelta

import pytest

from beacon import upload
from beacon.mapping import ADDED, CONVERTED, expected_columns
from beacon.upload import BadBody, TooLarge
from tests.helpers import RECEIVED_AT, context, lines, metadata, samples

VALID = samples("event", "valid")
INVALID = samples("event", "invalid")


def rows(result):
    return [dict(zip(upload.EVENT_INSERT_COLUMNS, row, strict=True)) for row in result.events]


def test_every_valid_sample_becomes_one_row_with_every_column():
    result = upload.process(lines(metadata(), list(VALID.values())), metadata(), context())

    assert result.rejected_count == 0
    assert len(result.events) == len(VALID)
    assert all(len(row) == len(upload.EVENT_INSERT_COLUMNS) for row in result.events)


@pytest.mark.parametrize("name", sorted(INVALID))
def test_every_invalid_sample_is_rejected_with_a_reason(name):
    result = upload.process(lines(metadata(), [INVALID[name]]), metadata(), context())

    assert result.events == []
    assert result.rejected_count == 1
    _, _, _, line, reason, error, raw = result.rejected[0]
    assert (line, reason) == (2, "invalid") and error and raw


def test_the_insert_columns_are_the_columns_the_mapping_check_expects():
    for table, schema, columns in [
        ("events", "event", upload.EVENT_INSERT_COLUMNS),
        ("uploads", "upload-metadata", upload.UPLOAD_INSERT_COLUMNS),
    ]:
        expected = set(expected_columns(schema)) | set(CONVERTED.get(table, {})) | ADDED[table]
        derived = {"weight", "wq", "outcome"} if table == "events" else set()
        assert set(columns) == expected - derived


def test_a_row_carries_the_upload_and_server_values():
    sample = VALID["tcp-refused"]
    carrier = dict(sample["network"]["carrier"], name="T-Mobile")
    event = dict(sample, network=dict(sample["network"], carrier=carrier))
    meta = metadata()
    row = rows(upload.process(lines(meta, [event]), meta, context()))[0]

    assert row["call_id"] == int(event["call_id"], 16)
    assert row["sim_country"] == "US"  # MCC 310
    assert (row["ip_country"], row["asn"], row["as_org"]) == ("NL", 1136, "KPN B.V.")
    assert row["app_version"] == meta["app_version"]
    assert row["network_carrier_name"] == event["network"]["carrier"]["name"]
    assert row["network_signal_strength"] is None
    assert row["attempts.result"] == ["failed"]


def test_event_time_is_the_server_time_minus_the_event_age():
    event = dict(VALID["ok-failover-on"], start_time=1_000_000_000)
    meta = metadata(sent_at=1_000_060_000)
    row = rows(upload.process(lines(meta, [event]), meta, context()))[0]

    assert row["event_time"] == RECEIVED_AT - timedelta(minutes=1)
    assert row["delivery_delay"] == 60_000
    assert row["event_time_clamped"] is False


@pytest.mark.parametrize(
    ("age_ms", "expected"),
    [(25 * 3_600_000, RECEIVED_AT - timedelta(hours=24)), (-5_000, RECEIVED_AT)],
)
def test_an_impossible_age_is_clamped(age_ms, expected):
    event = dict(VALID["ok-failover-on"], start_time=10_000_000_000)
    meta = metadata(sent_at=10_000_000_000 + age_ms)
    row = rows(upload.process(lines(meta, [event]), meta, context()))[0]

    assert (row["event_time"], row["event_time_clamped"]) == (expected, True)


def test_a_number_beyond_64_bits_is_rejected_not_stored():
    event = dict(VALID["ok-failover-on"], elapsed_time=2**70)
    result = upload.process(lines(metadata(), [event]), metadata(), context())

    assert result.events == [] and "64 bits" in result.rejected[0][5]


def test_stored_invalid_lines_are_capped_in_number_and_size():
    junk = [{"format_version": 1, "padding": "x" * 10_000}] * 150
    result = upload.process(lines(metadata(), junk), metadata(), context())

    assert result.rejected_count == 150
    assert len(result.rejected) == upload.STORED_INVALID_LINES
    assert max(len(r[6]) for r in result.rejected) == upload.STORED_LINE_BYTES


def test_lines_that_are_not_events_are_rejected():
    result = upload.process(
        [
            json.dumps(metadata()).encode(),
            b"not json",
            b"[1]",
            b'{"format_version": 2}',
            b'{"format_version": true}',
        ],
        metadata(),
        context(),
    )
    assert [r[5].split(":")[0] for r in result.rejected] == [
        "not JSON", "not an object", "unknown format_version 2", "unknown format_version True",
    ]  # fmt: skip


def test_invalid_metadata_refuses_the_body():
    meta = metadata()
    del meta["sent_at"]
    with pytest.raises(BadBody, match="sent_at"):
        upload.process(lines(meta, []), meta, context())


@pytest.mark.parametrize(
    "line",
    [
        b"not json",
        b"[]",
        b'{"app_package": "com.example.shop"}',
        b'{"schema_version": "1.0", "app_package": "com.example.shop"}',
        b'{"schema_version": "1.0.0"}',
    ],
)
def test_a_header_without_version_or_package_is_a_bad_body(line):
    with pytest.raises(BadBody):
        upload.read_header(line)


@pytest.mark.parametrize(
    ("version", "newer"),
    [("1.0.0", False), ("1.0.9", False), ("1.1.0", True), ("2.0.0", True), ("0.9.0", False)],
)
def test_only_a_newer_major_or_minor_is_newer(version, newer):
    assert upload.is_newer(version) is newer


def test_gunzip_stops_at_the_cap():
    with pytest.raises(TooLarge):
        upload.gunzip(gzip.compress(b"0" * (upload.DECOMPRESSED_CAP + 1)))
    assert (
        len(upload.gunzip(gzip.compress(b"0" * upload.DECOMPRESSED_CAP))) == upload.DECOMPRESSED_CAP
    )


@pytest.mark.parametrize(
    "data",
    [b"\x1f\x8b not gzip", gzip.compress(b"x")[:-4], gzip.compress(b"x") + b"trailing"],
)
def test_broken_gzip_is_a_bad_body(data):
    with pytest.raises(BadBody):
        upload.gunzip(data)


def test_too_many_lines_or_none_at_all():
    with pytest.raises(TooLarge):
        upload.split_lines(b"{}\n" * (upload.MAX_LINES + 1))
    with pytest.raises(BadBody):
        upload.split_lines(b"")
    assert upload.split_lines(b"a\nb\n") == [b"a", b"b"]


def test_the_row_of_a_refused_upload_keeps_what_fits():
    claimed = {"app_package": "com.other.app", "sent_at": "soon", "os_api_level": 2**70}
    result = upload.Result(status="package_mismatch", code=403, metadata=claimed)
    row = dict(zip(upload.UPLOAD_INSERT_COLUMNS, upload.upload_row(context(), result), strict=True))

    assert (row["status"], row["response_code"], row["app_package"]) == (
        "package_mismatch",
        403,
        "com.other.app",
    )
    assert (row["sent_at"], row["os_api_level"], row["dropped_expired"]) == (0, 0, None)
