CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.${DATASET_ID}.pipeline_runs` (
  run_id STRING NOT NULL,
  execution_id STRING,
  trigger_source STRING,
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
OPTIONS (require_partition_filter = TRUE);
ALTER TABLE `${PROJECT_ID}.${DATASET_ID}.pipeline_runs`
ADD COLUMN IF NOT EXISTS execution_id STRING;
ALTER TABLE `${PROJECT_ID}.${DATASET_ID}.pipeline_runs`
ADD COLUMN IF NOT EXISTS trigger_source STRING;
ALTER TABLE `${PROJECT_ID}.${DATASET_ID}.pipeline_runs`
SET OPTIONS (partition_expiration_days = NULL);

CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.${DATASET_ID}.pipeline_state` (
  source STRING NOT NULL,
  data_through TIMESTAMP,
  published_run_id STRING,
  status STRING NOT NULL,
  lease_run_id STRING,
  lease_acquired_at TIMESTAMP,
  lease_expires_at TIMESTAMP,
  updated_at TIMESTAMP NOT NULL
)
CLUSTER BY source, status;
ALTER TABLE `${PROJECT_ID}.${DATASET_ID}.pipeline_state`
ADD COLUMN IF NOT EXISTS lease_run_id STRING;
ALTER TABLE `${PROJECT_ID}.${DATASET_ID}.pipeline_state`
ADD COLUMN IF NOT EXISTS lease_acquired_at TIMESTAMP;
ALTER TABLE `${PROJECT_ID}.${DATASET_ID}.pipeline_state`
ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMP;

CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.${DATASET_ID}.pipeline_quality_events` (
  run_id STRING NOT NULL,
  window_start TIMESTAMP NOT NULL,
  window_end TIMESTAMP NOT NULL,
  check_name STRING NOT NULL,
  disposition STRING NOT NULL,
  severity STRING NOT NULL,
  failure_count INT64 NOT NULL,
  passed BOOL NOT NULL,
  observed_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(observed_at)
CLUSTER BY disposition, check_name, run_id
OPTIONS (require_partition_filter = FALSE);
