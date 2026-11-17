import time

from beacon import enrich, mapping, schema


def test_uuid7_is_version_7_and_ordered_by_time():
    first = enrich.uuid7()
    time.sleep(0.002)
    second = enrich.uuid7()

    assert (first.version, first.variant) == (7, "specified in RFC 4122")
    assert first < second
    assert abs((first.int >> 80) - time.time() * 1000) < 1000


def test_sim_country_comes_from_the_mcc():
    assert enrich.sim_country("250") == "RU"
    assert enrich.sim_country("204") == "NL"
    assert enrich.sim_country("901") == ""
    assert enrich.sim_country(None) == ""


def test_delivery_delay_fits_a_signed_64_bit_column():
    assert enrich.delivery_delay(2**64 - 1, 0) == 2**63 - 1
    assert enrich.delivery_delay(0, 2**64 - 1) == -(2**63 - 1)


def test_the_schema_copy_matches_its_hashes():
    assert schema.verify_copy() == []


def test_column_types_follow_the_schema():
    events = mapping.expected_columns("event")
    assert events["port"] == {"Nullable(UInt16)"}
    assert events["resend_count"] == {"UInt64"}
    assert events["response_content_length"] == {"Nullable(Int64)"}
    assert events["network_vpn"] == {"Nullable(Bool)"}
    assert events["attempts.elapsed_time"] == {"Array(Nullable(UInt64))"}
    assert events["dns_addresses"] == {"Array(String)", "Array(LowCardinality(String))"}
    assert events["format_version"] == {"UInt8"}
    assert mapping.expected_columns("upload-metadata")["dropped_expired"] == {"Nullable(UInt64)"}
