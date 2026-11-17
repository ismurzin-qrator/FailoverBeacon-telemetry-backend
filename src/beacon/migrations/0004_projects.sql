CREATE TABLE IF NOT EXISTS projects
(
    project_id LowCardinality(String),
    name String,
    any_package Bool,
    packages Array(String)
)
ENGINE = MergeTree
ORDER BY project_id
