CREATE TABLE IF NOT EXISTS rejected_events
(
    project_id LowCardinality(String) CODEC(ZSTD(3)),
    upload_id UUID,
    received_at DateTime64(3, 'UTC') CODEC(Delta(8), ZSTD(3)),
    line UInt32,
    reason LowCardinality(String) CODEC(ZSTD(3)),
    error String CODEC(ZSTD(3)),
    raw String CODEC(ZSTD(3))
)
ENGINE = MergeTree
PARTITION BY toDate(received_at)
ORDER BY (project_id, received_at, upload_id, line)
TTL toDate(received_at) + INTERVAL 30 DAY
SETTINGS ttl_only_drop_parts = 1
