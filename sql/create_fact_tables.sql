CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.${DATASET_ID}.http_request_events` (
  event_id STRING NOT NULL,
  request_ts TIMESTAMP NOT NULL,
  request_date DATE NOT NULL,
  endpoint_class STRING,
  method STRING,
  status INT64,
  latency_ms INT64,
  revision_name STRING,
  source_event_ts TIMESTAMP,
  materialized_at TIMESTAMP
)
PARTITION BY request_date
CLUSTER BY endpoint_class, status, revision_name
OPTIONS (require_partition_filter = TRUE, partition_expiration_days = ${FACT_RETENTION_DAYS});

CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.${DATASET_ID}.question_events` (
  event_id STRING NOT NULL,
  question_ts TIMESTAMP NOT NULL,
  question_date DATE NOT NULL,
  user_key STRING NOT NULL,
  roster_id STRING,
  request_id STRING,
  trace_id STRING,
  conversation_id STRING,
  turn_id STRING,
  message_id STRING,
  mode STRING,
  device_class STRING,
  endpoint_class STRING,
  valid_question BOOL,
  attachment_count INT64,
  primary_question_category STRING,
  question_categories ARRAY<STRING>,
  classification_status STRING,
  is_multi_intent BOOL,
  analytics_tasks ARRAY<STRING>,
  primary_product_key STRING,
  primary_product_name STRING,
  product_keys ARRAY<STRING>,
  product_names ARRAY<STRING>,
  product_candidate_count INT64,
  product_resolved_count INT64,
  producer_revision STRING,
  producer_git_sha STRING,
  source_event_ts TIMESTAMP,
  materialized_at TIMESTAMP
)
PARTITION BY question_date
CLUSTER BY roster_id, primary_question_category, primary_product_key, mode
OPTIONS (require_partition_filter = TRUE, partition_expiration_days = ${FACT_RETENTION_DAYS});

CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.${DATASET_ID}.answer_events` (
  event_id STRING NOT NULL,
  answer_ts TIMESTAMP NOT NULL,
  answer_date DATE NOT NULL,
  user_key STRING NOT NULL,
  roster_id STRING,
  request_id STRING,
  trace_id STRING,
  conversation_id STRING,
  turn_id STRING,
  message_id STRING,
  mode STRING,
  device_class STRING,
  terminal STRING,
  runtime_status STRING,
  failure_stage STRING,
  failure_code STRING,
  primary_question_category STRING,
  question_categories ARRAY<STRING>,
  classification_status STRING,
  is_multi_intent BOOL,
  analytics_tasks ARRAY<STRING>,
  primary_product_key STRING,
  primary_product_name STRING,
  product_keys ARRAY<STRING>,
  product_names ARRAY<STRING>,
  product_candidate_count INT64,
  product_resolved_count INT64,
  demand_total INT64,
  delivered_demand_count INT64,
  partial_demand_count INT64,
  omitted_demand_count INT64,
  system_fault_count INT64,
  citation_count INT64,
  supported_claim_count INT64,
  unsupported_claim_count INT64,
  total_latency_ms INT64,
  stage_latency_ms JSON,
  writer_error_code STRING,
  retry_count INT64,
  message_persisted BOOL,
  assistant_error_present BOOL,
  persistence_error_code STRING,
  measurement_available BOOL,
  complete_delivery BOOL,
  primary_failure_reason STRING,
  revision_name STRING,
  git_sha STRING,
  build_id STRING,
  source_event_ts TIMESTAMP,
  materialized_at TIMESTAMP
)
PARTITION BY answer_date
CLUSTER BY roster_id, terminal, primary_question_category, revision_name
OPTIONS (require_partition_filter = TRUE, partition_expiration_days = ${FACT_RETENTION_DAYS});

CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.${DATASET_ID}.answer_action_events` (
  event_id STRING NOT NULL,
  action_ts TIMESTAMP NOT NULL,
  action_date DATE NOT NULL,
  user_key STRING NOT NULL,
  roster_id STRING,
  request_id STRING,
  conversation_id STRING,
  turn_id STRING,
  message_id STRING,
  target_message_id STRING,
  action STRING,
  feedback STRING,
  mode STRING,
  request_mode STRING,
  client_origin STRING,
  source_event_ts TIMESTAMP,
  materialized_at TIMESTAMP
)
PARTITION BY action_date
CLUSTER BY roster_id, action, target_message_id
OPTIONS (require_partition_filter = TRUE, partition_expiration_days = ${FACT_RETENTION_DAYS});

CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.${DATASET_ID}.demand_events` (
  event_id STRING NOT NULL,
  question_event_id STRING NOT NULL,
  question_ts TIMESTAMP NOT NULL,
  question_date DATE NOT NULL,
  user_key STRING NOT NULL,
  roster_id STRING,
  demand_id STRING,
  demand_order INT64,
  question_category STRING,
  analytics_task STRING,
  product_keys ARRAY<STRING>,
  product_names ARRAY<STRING>,
  requirement STRING,
  delivery_state STRING,
  evidence_state STRING,
  system_fault STRING,
  reason_codes ARRAY<STRING>,
  source_event_ts TIMESTAMP,
  materialized_at TIMESTAMP
)
PARTITION BY question_date
CLUSTER BY question_category, delivery_state, roster_id
OPTIONS (require_partition_filter = TRUE, partition_expiration_days = ${FACT_RETENTION_DAYS});

CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.${DATASET_ID}.citation_events` (
  event_id STRING NOT NULL,
  answer_event_id STRING NOT NULL,
  answer_ts TIMESTAMP NOT NULL,
  answer_date DATE NOT NULL,
  user_key STRING NOT NULL,
  roster_id STRING,
  message_id STRING,
  citation_order INT64,
  source_type STRING,
  source_system STRING,
  document_key STRING,
  display_title STRING,
  page_number INT64,
  access_status STRING,
  trust_tier STRING,
  primary_product_key STRING,
  source_event_ts TIMESTAMP,
  materialized_at TIMESTAMP
)
PARTITION BY answer_date
CLUSTER BY source_type, primary_product_key, document_key
OPTIONS (require_partition_filter = TRUE, partition_expiration_days = ${FACT_RETENTION_DAYS});

CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.${DATASET_ID}.conversation_events` (
  event_id STRING NOT NULL,
  conversation_id STRING NOT NULL,
  user_key STRING NOT NULL,
  roster_id STRING,
  first_active_at TIMESTAMP,
  last_active_at TIMESTAMP,
  updated_date DATE NOT NULL,
  user_message_count INT64,
  assistant_message_count INT64,
  followup_count INT64,
  active_days INT64,
  primary_mode STRING,
  status STRING,
  source_event_ts TIMESTAMP,
  materialized_at TIMESTAMP
)
PARTITION BY updated_date
CLUSTER BY roster_id, primary_mode, status
OPTIONS (require_partition_filter = TRUE, partition_expiration_days = ${FACT_RETENTION_DAYS});

CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.${DATASET_ID}.user_scope` (
  roster_id STRING NOT NULL,
  user_key STRING NOT NULL,
  area STRING,
  area_key STRING,
  workplace STRING,
  role STRING,
  department STRING,
  mr_experience STRING,
  is_active BOOL,
  global_scope_enabled BOOL,
  user_map_scope_enabled BOOL,
  is_admin BOOL,
  valid_from TIMESTAMP NOT NULL,
  valid_to TIMESTAMP,
  updated_at TIMESTAMP NOT NULL
)
CLUSTER BY roster_id, user_key, area_key, department;
