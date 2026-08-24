CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.${DATASET_ID}.user_daily` (
  activity_date DATE NOT NULL,
  roster_id STRING NOT NULL,
  area_key STRING,
  area STRING,
  role STRING,
  department STRING,
  active BOOL,
  last_active_at TIMESTAMP,
  question_count INT64,
  materialized_at TIMESTAMP
)
PARTITION BY activity_date
CLUSTER BY roster_id, area_key, department
OPTIONS (require_partition_filter = TRUE, partition_expiration_days = ${AGGREGATE_RETENTION_DAYS});

CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.${DATASET_ID}.pipeline_runs` (
  run_id STRING NOT NULL,
  started_at TIMESTAMP NOT NULL,
  finished_at TIMESTAMP,
  window_start TIMESTAMP NOT NULL,
  window_end TIMESTAMP NOT NULL,
  source STRING NOT NULL,
  status STRING NOT NULL,
  input_rows INT64,
  merged_rows INT64,
  duplicate_rows INT64,
  bytes_processed INT64,
  error_code STRING
)
PARTITION BY DATE(started_at)
CLUSTER BY source, status
OPTIONS (require_partition_filter = TRUE, partition_expiration_days = ${AGGREGATE_RETENTION_DAYS});

CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.${DATASET_ID}.pipeline_state` (
  source STRING NOT NULL,
  data_through TIMESTAMP,
  published_run_id STRING,
  status STRING NOT NULL,
  updated_at TIMESTAMP NOT NULL
)
CLUSTER BY source, status;
