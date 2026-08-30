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
ADD COLUMN IF NOT EXISTS execution_id STRING,
ADD COLUMN IF NOT EXISTS trigger_source STRING;
ALTER TABLE `${PROJECT_ID}.${DATASET_ID}.pipeline_runs`
SET OPTIONS (partition_expiration_days = NULL);

CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.${DATASET_ID}.pipeline_state` (
  source STRING NOT NULL,
  data_through TIMESTAMP,
  published_run_id STRING,
  scope_policy_version STRING,
  global_roster_fingerprint STRING,
  global_content_fingerprint STRING,
  user_map_roster_fingerprint STRING,
  user_map_content_fingerprint STRING,
  status STRING NOT NULL,
  lease_run_id STRING,
  lease_acquired_at TIMESTAMP,
  lease_expires_at TIMESTAMP,
  updated_at TIMESTAMP NOT NULL
)
CLUSTER BY source, status;
ALTER TABLE `${PROJECT_ID}.${DATASET_ID}.pipeline_state`
ADD COLUMN IF NOT EXISTS lease_run_id STRING,
ADD COLUMN IF NOT EXISTS lease_acquired_at TIMESTAMP,
ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMP,
ADD COLUMN IF NOT EXISTS scope_policy_version STRING,
ADD COLUMN IF NOT EXISTS global_roster_fingerprint STRING,
ADD COLUMN IF NOT EXISTS global_content_fingerprint STRING,
ADD COLUMN IF NOT EXISTS user_map_roster_fingerprint STRING,
ADD COLUMN IF NOT EXISTS user_map_content_fingerprint STRING;

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

-- Small, persistent rollout ledger for the producer's strict HTTP correlation
-- contract. A Cloud Run revision is immutable, so one row per revision is the
-- durable owner. Only the controlled exact candidate-sample registration may
-- write this table; the recurring incremental publisher only reads it.
CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.${DATASET_ID}.monitor_contract_revision_ledger` (
  revision_name STRING NOT NULL,
  monitor_contract_version STRING NOT NULL,
  sample_source_ts TIMESTAMP NOT NULL,
  sample_endpoint_class STRING NOT NULL,
  sample_cloud_trace STRING NOT NULL,
  sample_cloud_span_id STRING NOT NULL,
  sample_correlation_hash STRING NOT NULL,
  registration_source STRING NOT NULL,
  enforcement_start TIMESTAMP,
  activation_source STRING,
  promotion_receipt_type STRING,
  promotion_receipt_sha256 STRING,
  promotion_project STRING,
  promotion_region STRING,
  promotion_service STRING,
  promotion_target_revision STRING,
  promotion_traffic_readback_at TIMESTAMP,
  promotion_max_request_timeout_seconds INT64,
  promotion_drain_until TIMESTAMP,
  promotion_old_positive_revisions_json STRING,
  activation_service_readback_sha256 STRING,
  registered_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL
)
CLUSTER BY monitor_contract_version, revision_name;
ALTER TABLE `${PROJECT_ID}.${DATASET_ID}.monitor_contract_revision_ledger`
ADD COLUMN IF NOT EXISTS sample_cloud_trace STRING,
ADD COLUMN IF NOT EXISTS sample_cloud_span_id STRING,
ADD COLUMN IF NOT EXISTS enforcement_start TIMESTAMP,
ADD COLUMN IF NOT EXISTS activation_source STRING,
ADD COLUMN IF NOT EXISTS promotion_receipt_type STRING,
ADD COLUMN IF NOT EXISTS promotion_receipt_sha256 STRING,
ADD COLUMN IF NOT EXISTS promotion_project STRING,
ADD COLUMN IF NOT EXISTS promotion_region STRING,
ADD COLUMN IF NOT EXISTS promotion_service STRING,
ADD COLUMN IF NOT EXISTS promotion_target_revision STRING,
ADD COLUMN IF NOT EXISTS promotion_traffic_readback_at TIMESTAMP,
ADD COLUMN IF NOT EXISTS promotion_max_request_timeout_seconds INT64,
ADD COLUMN IF NOT EXISTS promotion_drain_until TIMESTAMP,
ADD COLUMN IF NOT EXISTS promotion_old_positive_revisions_json STRING,
ADD COLUMN IF NOT EXISTS activation_service_readback_sha256 STRING;
