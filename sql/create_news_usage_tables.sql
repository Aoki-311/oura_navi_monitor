-- Additive News/Society usage storage.  This file must be applied before the
-- producer or MONITOR_NEWS_USAGE_* runtime settings are enabled.
ALTER TABLE `${PROJECT_ID}.${DATASET_ID}.pipeline_state`
ADD COLUMN IF NOT EXISTS roster_snapshot_run_id STRING,
ADD COLUMN IF NOT EXISTS measurement_start_at TIMESTAMP,
ADD COLUMN IF NOT EXISTS source_service STRING;

CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.${DATASET_ID}.news_usage_events` (
  event_id STRING NOT NULL,
  event_content_hash STRING NOT NULL,
  usage_event_id STRING NOT NULL,
  page_view_id STRING NOT NULL,
  event_name STRING NOT NULL,
  channel STRING NOT NULL,
  occurred_at TIMESTAMP NOT NULL,
  usage_date_jst DATE NOT NULL,
  user_id STRING NOT NULL,
  actor_email_hash STRING,
  ingested_roster_id STRING NOT NULL,
  ingested_roster_snapshot_run_id STRING NOT NULL,
  content_event_id STRING,
  content_event_version STRING,
  content_event_type STRING,
  content_domain_key STRING,
  content_geography_scope STRING,
  content_source_id STRING,
  content_category_key STRING,
  source_catalog_version STRING,
  filter_snapshot_present BOOL NOT NULL,
  filter_domain_keys ARRAY<STRING>,
  filter_source_ids ARRAY<STRING>,
  filter_category_keys ARRAY<STRING>,
  filter_event_types ARRAY<STRING>,
  filter_news_geography_scope STRING,
  filter_start_date DATE,
  filter_end_date DATE,
  filter_has_query BOOL,
  changed_fields ARRAY<STRING>,
  surface STRING,
  trigger STRING,
  link_kind STRING,
  operation_id STRING,
  result STRING,
  error_code STRING,
  summary_date_jst DATE,
  producer_revision STRING,
  producer_git_sha STRING,
  producer_build_id STRING,
  source_service STRING NOT NULL,
  source_ts TIMESTAMP NOT NULL,
  first_run_id STRING NOT NULL,
  last_run_id STRING NOT NULL,
  first_seen_at TIMESTAMP NOT NULL,
  last_seen_at TIMESTAMP NOT NULL,
  materialized_at TIMESTAMP NOT NULL
)
PARTITION BY usage_date_jst
CLUSTER BY event_name, channel, ingested_roster_id, source_service
OPTIONS (require_partition_filter = FALSE);
ALTER TABLE `${PROJECT_ID}.${DATASET_ID}.news_usage_events`
ADD COLUMN IF NOT EXISTS actor_email_hash STRING;
ALTER TABLE `${PROJECT_ID}.${DATASET_ID}.news_usage_events`
ADD COLUMN IF NOT EXISTS content_event_type STRING;

-- Privacy-safe operational diagnostics.  Raw ids, users and submitted payloads
-- are deliberately absent; only a one-way delivery hash and closed codes remain.
CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.${DATASET_ID}.news_usage_event_issues` (
  source_event_hash STRING NOT NULL,
  issue_code STRING NOT NULL,
  disposition STRING NOT NULL,
  event_name STRING,
  channel STRING,
  source_ts TIMESTAMP NOT NULL,
  event_ts TIMESTAMP,
  first_run_id STRING NOT NULL,
  last_run_id STRING NOT NULL,
  first_observed_at TIMESTAMP NOT NULL,
  last_observed_at TIMESTAMP NOT NULL,
  observation_count INT64 NOT NULL,
  resolution_status STRING NOT NULL,
  resolved_at TIMESTAMP
)
CLUSTER BY resolution_status, disposition, issue_code, event_name;

-- This small pointer is the sole public availability owner.  It also tells a
-- reader which immutable user_scope snapshot the usage publication references.
CREATE OR REPLACE VIEW `${PROJECT_ID}.${DATASET_ID}.news_usage_publication_state` AS
SELECT
  source,
  status,
  measurement_start_at,
  data_through,
  published_run_id,
  roster_snapshot_run_id,
  source_service,
  scope_policy_version,
  global_roster_fingerprint,
  global_content_fingerprint,
  user_map_roster_fingerprint,
  user_map_content_fingerprint,
  updated_at
FROM `${PROJECT_ID}.${DATASET_ID}.pipeline_state`
WHERE source = 'news_usage';

-- Cumulative facts are exposed only behind a successful atomic publication.
-- Identity is resolved once during admission.  The stable employee roster_id
-- then binds historical facts to current roster attributes even when a subject
-- or email later changes.  The email digest remains private.
CREATE OR REPLACE VIEW `${PROJECT_ID}.${DATASET_ID}.news_usage_published_events` AS
WITH publication AS (
  SELECT state.*
  FROM `${PROJECT_ID}.${DATASET_ID}.pipeline_state` state
  WHERE state.source = 'news_usage'
    AND state.status = 'succeeded'
    AND state.published_run_id IS NOT NULL
), current_roster AS (
  SELECT
    roster.snapshot_run_id,
    roster.roster_id,
    COUNT(*) AS matching_rows
  FROM `${PROJECT_ID}.${DATASET_ID}.user_scope` roster
  CROSS JOIN publication state
  WHERE roster.snapshot_run_id = state.roster_snapshot_run_id
    AND roster.user_map_scope_enabled = TRUE
    AND roster.is_active = TRUE
  GROUP BY roster.snapshot_run_id, roster.roster_id
  HAVING matching_rows = 1
)
SELECT
  events.* EXCEPT (actor_email_hash),
  roster.roster_id AS roster_id,
  state.roster_snapshot_run_id AS roster_snapshot_run_id,
  state.published_run_id AS publication_run_id,
  state.data_through AS publication_data_through
FROM `${PROJECT_ID}.${DATASET_ID}.news_usage_events` events
CROSS JOIN publication state
JOIN current_roster roster
  ON roster.snapshot_run_id = state.roster_snapshot_run_id
  AND roster.roster_id = events.ingested_roster_id
WHERE events.source_service = state.source_service
  AND events.source_ts < state.data_through;
