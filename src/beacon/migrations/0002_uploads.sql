CREATE TABLE IF NOT EXISTS uploads
(
    project_id LowCardinality(String) CODEC(ZSTD(3)),
    upload_id UUID,
    received_at DateTime64(3, 'UTC') CODEC(Delta(8), ZSTD(3)),
    status LowCardinality(String) CODEC(ZSTD(3)),
    response_code UInt16,
    events UInt32,
    rejected UInt32,
    body_bytes UInt64,
    decompressed_bytes UInt64,
    ip_country LowCardinality(String) CODEC(ZSTD(3)),
    asn Nullable(UInt32),
    as_org LowCardinality(String) CODEC(ZSTD(3)),
    geo_database_date Nullable(Date),

    sent_at UInt64 CODEC(Delta(8), ZSTD(3)),
    sdk_version LowCardinality(String) CODEC(ZSTD(3)),
    schema_version LowCardinality(String) CODEC(ZSTD(3)),
    app_package LowCardinality(String) CODEC(ZSTD(3)),
    app_version LowCardinality(String) CODEC(ZSTD(3)),
    os_api_level UInt64,
    dropped_memory_overflow Nullable(UInt64),
    dropped_disk_overflow Nullable(UInt64),
    dropped_expired Nullable(UInt64),
    dropped_send_error Nullable(UInt64)
)
ENGINE = MergeTree
PARTITION BY toDate(received_at)
ORDER BY (project_id, received_at)
TTL toDate(received_at) + INTERVAL 90 DAY
SETTINGS ttl_only_drop_parts = 1
