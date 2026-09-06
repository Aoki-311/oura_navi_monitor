-- Required parameters:
-- @run_id, @lease_id, @expected_watermark, @window_start, @window_end,
-- @measurement_start, @event_future_tolerance_minutes, @source_service,
-- @roster_snapshot_run_id, @scope_policy_version and four roster fingerprints.
--
-- This transaction is deliberately separate from Chat publication.  It may
-- read the successful Chat roster pointer, but cannot mutate Chat facts/state.
BEGIN TRANSACTION;

ASSERT EXISTS (
  SELECT 1
  FROM `${PROJECT_ID}.${DATASET_ID}.pipeline_state`
  WHERE source = 'news_usage'
    AND lease_run_id = @lease_id
    AND lease_expires_at > CURRENT_TIMESTAMP()
    AND source_service = @source_service
    AND measurement_start_at = @measurement_start
    AND (
      (@expected_watermark IS NULL AND data_through IS NULL)
      OR data_through = @expected_watermark
    )
) AS 'news usage lease, configuration or expected watermark changed';

ASSERT EXISTS (
  SELECT 1
  FROM `${PROJECT_ID}.${DATASET_ID}.pipeline_state`
  WHERE source = 'published'
    AND status = 'succeeded'
    AND published_run_id = @roster_snapshot_run_id
    AND scope_policy_version = @scope_policy_version
    AND global_roster_fingerprint = @global_roster_fingerprint
    AND global_content_fingerprint = @global_content_fingerprint
    AND user_map_roster_fingerprint = @user_map_roster_fingerprint
    AND user_map_content_fingerprint = @user_map_content_fingerprint
) AS 'referenced Chat roster publication changed';

ASSERT EXISTS (
  SELECT 1
  FROM `${PROJECT_ID}.${DATASET_ID}.user_scope`
  WHERE snapshot_run_id = @roster_snapshot_run_id
) AS 'referenced user scope snapshot is missing';

CREATE TEMP TABLE _news_usage_roster_scope AS
SELECT
  roster_id,
  user_id,
  IF(
    NULLIF(TRIM(email), '') IS NULL,
    NULL,
    LOWER(TO_HEX(SHA256(
      NORMALIZE_AND_CASEFOLD(TRIM(email), NFKC)
    )))
  ) AS actor_email_hash,
  updated_at
FROM `${PROJECT_ID}.${DATASET_ID}.user_scope`
WHERE snapshot_run_id = @roster_snapshot_run_id
  AND user_map_scope_enabled = TRUE;

CREATE TEMP TABLE _news_usage_scope_by_subject AS
SELECT
  user_id,
  ARRAY_AGG(roster_id ORDER BY updated_at DESC, roster_id LIMIT 1)[OFFSET(0)]
    AS subject_roster_id,
  COUNT(DISTINCT roster_id) AS subject_match_count
FROM _news_usage_roster_scope
WHERE NULLIF(user_id, '') IS NOT NULL
GROUP BY user_id;

CREATE TEMP TABLE _news_usage_scope_by_email_hash AS
SELECT
  actor_email_hash,
  ARRAY_AGG(roster_id ORDER BY updated_at DESC, roster_id LIMIT 1)[OFFSET(0)]
    AS email_roster_id,
  COUNT(DISTINCT roster_id) AS email_match_count
FROM _news_usage_roster_scope
WHERE actor_email_hash IS NOT NULL
GROUP BY actor_email_hash;

CREATE TEMP TABLE _news_usage_source AS
WITH parsed AS (
  SELECT
    source.*,
    SAFE.PARSE_JSON(source.payload_json) AS payload,
    TO_HEX(SHA256(TO_JSON_STRING(STRUCT(
      source.insert_id,
      source.source_ts,
      source.event_id,
      source.event_ts,
      source.user_id,
      source.payload_json
    )))) AS source_event_hash,
    TO_HEX(SHA256(TO_JSON_STRING(STRUCT(
      source.event_id,
      source.event_ts,
      source.user_id,
      TO_JSON_STRING(SAFE.PARSE_JSON(source.payload_json)) AS canonical_payload
    )))) AS event_content_hash
  FROM `${PROJECT_ID}.${DATASET_ID}.news_usage_event_source` source
  WHERE source.source_ts >= @window_start
    AND source.source_ts < @window_end
), extracted AS (
  SELECT
    parsed.*,
    JSON_VALUE(payload, '$.schema_version') AS schema_version,
    JSON_VALUE(payload, '$.usage_event_id') AS usage_event_id,
    JSON_VALUE(payload, '$.page_view_id') AS page_view_id,
    JSON_VALUE(payload, '$.event_name') AS payload_event_name,
    JSON_VALUE(payload, '$.channel') AS payload_channel,
    SAFE_CAST(JSON_VALUE(payload, '$.occurred_at') AS TIMESTAMP) AS occurred_at,
    JSON_VALUE(payload, '$.content_event_id') AS raw_content_event_id,
    JSON_VALUE(payload, '$.content_event_version') AS raw_content_event_version,
    JSON_VALUE(payload, '$.content_event_type') AS raw_content_event_type,
    JSON_VALUE(payload, '$.content_domain_key') AS raw_content_domain_key,
    JSON_VALUE(payload, '$.content_geography_scope') AS raw_content_geography_scope,
    JSON_VALUE(payload, '$.content_source_id') AS raw_content_source_id,
    JSON_VALUE(payload, '$.content_category_key') AS raw_content_category_key,
    JSON_VALUE(payload, '$.source_catalog_version') AS raw_source_catalog_version,
    COALESCE(
      JSON_TYPE(JSON_QUERY(payload, '$.filter_snapshot')) = 'object',
      FALSE
    ) AS filter_snapshot_present,
    JSON_QUERY(payload, '$.filter_snapshot.domain_keys')
      AS raw_filter_domain_keys,
    JSON_QUERY(payload, '$.filter_snapshot.source_ids')
      AS raw_filter_source_ids,
    JSON_QUERY(payload, '$.filter_snapshot.category_keys')
      AS raw_filter_category_keys,
    JSON_QUERY(payload, '$.filter_snapshot.event_types')
      AS raw_filter_event_types,
    JSON_VALUE(payload, '$.filter_snapshot.news_geography_scope')
      AS raw_filter_news_geography_scope,
    JSON_VALUE(payload, '$.filter_snapshot.start_date') AS raw_filter_start_date,
    JSON_VALUE(payload, '$.filter_snapshot.end_date') AS raw_filter_end_date,
    JSON_VALUE(payload, '$.filter_snapshot.has_query') AS raw_filter_has_query,
    JSON_QUERY(payload, '$.changed_fields') AS raw_changed_fields,
    JSON_VALUE(payload, '$.surface') AS raw_surface,
    JSON_VALUE(payload, '$.trigger') AS raw_trigger,
    JSON_VALUE(payload, '$.link_kind') AS raw_link_kind,
    JSON_VALUE(payload, '$.operation_id') AS raw_operation_id,
    JSON_VALUE(payload, '$.result') AS raw_result,
    JSON_VALUE(payload, '$.error_code') AS raw_error_code,
    JSON_VALUE(payload, '$.summary_date_jst') AS raw_summary_date_jst,
    ARRAY(
      SELECT JSON_VALUE(item)
      FROM UNNEST(IFNULL(
        JSON_QUERY_ARRAY(metadata_issues_json), []
      )) item
      WHERE JSON_TYPE(item) = 'string'
    ) AS producer_metadata_issues
  FROM parsed
), normalized AS (
  SELECT
    extracted.*,
    subject.subject_roster_id,
    COALESCE(subject.subject_match_count, 0) AS subject_match_count,
    email.email_roster_id,
    COALESCE(email.email_match_count, 0) AS email_match_count,
    IF(
      REGEXP_CONTAINS(COALESCE(extracted.actor_email_hash, ''), r'^[0-9a-f]{64}$'),
      extracted.actor_email_hash,
      NULL
    ) AS normalized_actor_email_hash,
    CASE
      WHEN COALESCE(subject.subject_match_count, 0) = 1
        AND COALESCE(email.email_match_count, 0) <= 1
        AND (
          COALESCE(email.email_match_count, 0) = 0
          OR subject.subject_roster_id = email.email_roster_id
        )
        THEN subject.subject_roster_id
      WHEN COALESCE(subject.subject_match_count, 0) = 0
        AND COALESCE(email.email_match_count, 0) = 1
        THEN email.email_roster_id
      ELSE NULL
    END AS roster_id,
    IF(
      REGEXP_CONTAINS(COALESCE(raw_content_event_id, ''), r'^[A-Za-z0-9_.:-]{1,160}$'),
      raw_content_event_id,
      NULL
    ) AS content_event_id,
    IF(
      REGEXP_CONTAINS(COALESCE(raw_content_event_version, ''), r'^[A-Za-z0-9_.:-]{1,160}$'),
      raw_content_event_version,
      NULL
    ) AS content_event_version,
    IF(
      REGEXP_CONTAINS(COALESCE(raw_content_event_type, ''), r'^[A-Za-z0-9_.:-]{1,160}$'),
      raw_content_event_type,
      NULL
    ) AS content_event_type,
    IF(
      REGEXP_CONTAINS(COALESCE(raw_content_domain_key, ''), r'^[A-Za-z0-9_.:-]{1,160}$'),
      raw_content_domain_key,
      NULL
    ) AS content_domain_key,
    IF(
      raw_content_geography_scope IN ('domestic', 'overseas'),
      raw_content_geography_scope,
      NULL
    ) AS content_geography_scope,
    IF(
      REGEXP_CONTAINS(COALESCE(raw_content_source_id, ''), r'^[A-Za-z0-9_.:-]{1,160}$'),
      raw_content_source_id,
      NULL
    ) AS content_source_id,
    IF(
      raw_content_category_key IS NOT NULL
        AND LENGTH(raw_content_category_key) BETWEEN 1 AND 100
        AND NOT REGEXP_CONTAINS(raw_content_category_key, r'[\x00-\x1f@/\\]'),
      raw_content_category_key,
      NULL
    ) AS content_category_key,
    IF(
      REGEXP_CONTAINS(COALESCE(raw_source_catalog_version, ''), r'^[A-Za-z0-9_.:-]{1,160}$'),
      raw_source_catalog_version,
      NULL
    ) AS source_catalog_version,
    IF(
      JSON_TYPE(raw_filter_domain_keys) = 'array'
        AND ARRAY_LENGTH(JSON_QUERY_ARRAY(raw_filter_domain_keys)) <= 64
        AND NOT EXISTS (
          SELECT 1 FROM UNNEST(JSON_QUERY_ARRAY(raw_filter_domain_keys)) item
          WHERE JSON_TYPE(item) != 'string'
            OR NOT REGEXP_CONTAINS(COALESCE(JSON_VALUE(item), ''), r'^[A-Za-z0-9_.:-]{1,160}$')
        ),
      ARRAY(
        SELECT JSON_VALUE(item)
        FROM UNNEST(JSON_QUERY_ARRAY(raw_filter_domain_keys)) item
      ),
      []
    ) AS filter_domain_keys,
    IF(
      JSON_TYPE(raw_filter_source_ids) = 'array'
        AND ARRAY_LENGTH(JSON_QUERY_ARRAY(raw_filter_source_ids)) <= 64
        AND NOT EXISTS (
          SELECT 1 FROM UNNEST(JSON_QUERY_ARRAY(raw_filter_source_ids)) item
          WHERE JSON_TYPE(item) != 'string'
            OR NOT REGEXP_CONTAINS(COALESCE(JSON_VALUE(item), ''), r'^[A-Za-z0-9_.:-]{1,160}$')
        ),
      ARRAY(
        SELECT JSON_VALUE(item)
        FROM UNNEST(JSON_QUERY_ARRAY(raw_filter_source_ids)) item
      ),
      []
    ) AS filter_source_ids,
    IF(
      JSON_TYPE(raw_filter_category_keys) = 'array'
        AND ARRAY_LENGTH(JSON_QUERY_ARRAY(raw_filter_category_keys)) <= 32
        AND NOT EXISTS (
          SELECT 1 FROM UNNEST(JSON_QUERY_ARRAY(raw_filter_category_keys)) item
          WHERE JSON_TYPE(item) != 'string'
            OR LENGTH(COALESCE(JSON_VALUE(item), '')) NOT BETWEEN 1 AND 100
            OR REGEXP_CONTAINS(COALESCE(JSON_VALUE(item), ''), r'[\x00-\x1f@/\\]')
        ),
      ARRAY(
        SELECT JSON_VALUE(item)
        FROM UNNEST(JSON_QUERY_ARRAY(raw_filter_category_keys)) item
      ),
      []
    ) AS filter_category_keys,
    IF(
      JSON_TYPE(raw_filter_event_types) = 'array'
        AND ARRAY_LENGTH(JSON_QUERY_ARRAY(raw_filter_event_types)) <= 64
        AND NOT EXISTS (
          SELECT 1 FROM UNNEST(JSON_QUERY_ARRAY(raw_filter_event_types)) item
          WHERE JSON_TYPE(item) != 'string'
            OR NOT REGEXP_CONTAINS(COALESCE(JSON_VALUE(item), ''), r'^[A-Za-z0-9_.:-]{1,160}$')
        ),
      ARRAY(
        SELECT JSON_VALUE(item)
        FROM UNNEST(JSON_QUERY_ARRAY(raw_filter_event_types)) item
      ),
      []
    ) AS filter_event_types,
    IF(
      raw_filter_news_geography_scope IN ('domestic', 'overseas'),
      raw_filter_news_geography_scope,
      NULL
    ) AS filter_news_geography_scope,
    SAFE_CAST(raw_filter_start_date AS DATE) AS filter_start_date,
    SAFE_CAST(raw_filter_end_date AS DATE) AS filter_end_date,
    SAFE_CAST(raw_filter_has_query AS BOOL) AS filter_has_query,
    IF(
      JSON_TYPE(raw_changed_fields) = 'array'
        AND ARRAY_LENGTH(JSON_QUERY_ARRAY(raw_changed_fields)) BETWEEN 1 AND 8
        AND NOT EXISTS (
          SELECT 1 FROM UNNEST(JSON_QUERY_ARRAY(raw_changed_fields)) item
          WHERE JSON_TYPE(item) != 'string'
            OR COALESCE(JSON_VALUE(item), '') NOT IN (
            'domain_keys', 'source_ids', 'category_keys', 'event_types',
            'news_geography_scope', 'start_date', 'end_date', 'query'
          )
        ),
      ARRAY(
        SELECT JSON_VALUE(item)
        FROM UNNEST(JSON_QUERY_ARRAY(raw_changed_fields)) item
      ),
      []
    ) AS changed_fields,
    IF(raw_surface IN ('list', 'detail', 'summary'), raw_surface, NULL) AS surface,
    IF(raw_trigger IN ('initial', 'switch', 'manual', 'auto'), raw_trigger, NULL)
      AS trigger,
    IF(raw_link_kind IN ('primary', 'evidence', 'registration'), raw_link_kind, NULL)
      AS link_kind,
    IF(
      REGEXP_CONTAINS(
        COALESCE(raw_operation_id, ''),
        r'(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
      ),
      LOWER(raw_operation_id),
      NULL
    ) AS operation_id,
    IF(raw_result IN ('download_handed_off', 'cancelled', 'failed'), raw_result, NULL)
      AS result,
    IF(raw_error_code = 'export_request_failed', raw_error_code, NULL) AS error_code,
    SAFE_CAST(raw_summary_date_jst AS DATE) AS summary_date_jst
  FROM extracted
  LEFT JOIN _news_usage_scope_by_subject subject
    ON subject.user_id = extracted.user_id
  LEFT JOIN _news_usage_scope_by_email_hash email
    ON email.actor_email_hash = extracted.actor_email_hash
), ranked AS (
  SELECT
    normalized.*,
    ROW_NUMBER() OVER (
      PARTITION BY COALESCE(NULLIF(event_id, ''), source_event_hash)
      ORDER BY source_ts DESC, insert_id DESC
    ) AS delivery_row_number,
    MIN(event_content_hash) OVER (
      PARTITION BY COALESCE(NULLIF(event_id, ''), source_event_hash)
    ) AS minimum_content_hash,
    MAX(event_content_hash) OVER (
      PARTITION BY COALESCE(NULLIF(event_id, ''), source_event_hash)
    ) AS maximum_content_hash,
    IF(
      payload_event_name = 'export_finished' AND operation_id IS NOT NULL,
      DENSE_RANK() OVER (
        PARTITION BY user_id, payload_event_name, payload_channel, operation_id
        ORDER BY occurred_at, event_id
      ),
      1
    ) AS export_terminal_row_number
  FROM normalized
)
SELECT * FROM ranked;

CREATE TEMP TABLE _news_usage_issues AS
SELECT source_event_hash, source_ts, event_ts, payload_event_name, payload_channel,
  'source_payload_invalid' AS issue_code, 'row_quarantined' AS disposition
FROM _news_usage_source WHERE payload IS NULL
UNION ALL
SELECT source_event_hash, source_ts, event_ts, payload_event_name, payload_channel,
  'source_event_id_invalid', 'row_quarantined'
FROM _news_usage_source
WHERE NOT REGEXP_CONTAINS(COALESCE(event_id, ''), r'^news_usage:[0-9a-f]{64}$')
UNION ALL
SELECT source_event_hash, source_ts, event_ts, payload_event_name, payload_channel,
  'source_event_identity_mismatch', 'row_quarantined'
FROM _news_usage_source
WHERE event_id != CONCAT(
  'news_usage:',
  LOWER(TO_HEX(SHA256(CONCAT(COALESCE(user_id, ''), '\n', COALESCE(usage_event_id, '')))))
)
UNION ALL
SELECT source_event_hash, source_ts, event_ts, payload_event_name, payload_channel,
  'source_user_id_missing', 'row_quarantined'
FROM _news_usage_source WHERE NULLIF(user_id, '') IS NULL
UNION ALL
SELECT source_event_hash, source_ts, event_ts, payload_event_name, payload_channel,
  'source_actor_email_hash_invalid', 'axis_omitted'
FROM _news_usage_source
WHERE actor_email_hash IS NOT NULL AND normalized_actor_email_hash IS NULL
UNION ALL
SELECT source_event_hash, source_ts, event_ts, payload_event_name, payload_channel,
  'source_service_claim_mismatch', 'row_quarantined'
FROM _news_usage_source
WHERE NULLIF(claimed_service_name, '') IS NOT NULL
  AND claimed_service_name != source_service
UNION ALL
SELECT source_event_hash, source_ts, event_ts, payload_event_name, payload_channel,
  'usage_contract_invalid', 'row_quarantined'
FROM _news_usage_source
WHERE COALESCE(event_family, '') != 'news_usage'
  OR COALESCE(monitor_contract_version, '') != '${NEWS_USAGE_CONTRACT_VERSION}'
  OR COALESCE(schema_version, '') != '${NEWS_USAGE_CONTRACT_VERSION}'
UNION ALL
SELECT source_event_hash, source_ts, event_ts, payload_event_name, payload_channel,
  'usage_event_id_invalid', 'row_quarantined'
FROM _news_usage_source
WHERE NOT REGEXP_CONTAINS(
  COALESCE(usage_event_id, ''),
  r'(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
)
UNION ALL
SELECT source_event_hash, source_ts, event_ts, payload_event_name, payload_channel,
  'page_view_id_invalid', 'row_quarantined'
FROM _news_usage_source
WHERE NOT REGEXP_CONTAINS(
  COALESCE(page_view_id, ''),
  r'(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
)
UNION ALL
SELECT source_event_hash, source_ts, event_ts, payload_event_name, payload_channel,
  'event_name_invalid', 'row_quarantined'
FROM _news_usage_source
WHERE COALESCE(payload_event_name, '') NOT IN (
  'tab_view', 'filter_change', 'detail_view', 'outbound_click',
  'export_started', 'export_finished', 'summary_view'
)
UNION ALL
SELECT source_event_hash, source_ts, event_ts, payload_event_name, payload_channel,
  'channel_invalid', 'row_quarantined'
FROM _news_usage_source
WHERE COALESCE(payload_channel, '') NOT IN ('news', 'society')
UNION ALL
SELECT source_event_hash, source_ts, event_ts, payload_event_name, payload_channel,
  'event_timestamp_invalid', 'row_quarantined'
FROM _news_usage_source
WHERE occurred_at IS NULL OR event_ts IS NULL OR occurred_at != event_ts
UNION ALL
SELECT source_event_hash, source_ts, event_ts, payload_event_name, payload_channel,
  'event_timestamp_before_measurement_start', 'row_quarantined'
FROM _news_usage_source WHERE occurred_at < @measurement_start
UNION ALL
SELECT source_event_hash, source_ts, event_ts, payload_event_name, payload_channel,
  'event_timestamp_in_future', 'row_quarantined'
FROM _news_usage_source
WHERE occurred_at > TIMESTAMP_ADD(
  source_ts, INTERVAL @event_future_tolerance_minutes MINUTE
)
UNION ALL
SELECT source_event_hash, source_ts, event_ts, payload_event_name, payload_channel,
  'source_event_without_roster', 'row_quarantined'
FROM _news_usage_source
WHERE NULLIF(user_id, '') IS NOT NULL
  AND subject_match_count = 0
  AND email_match_count = 0
UNION ALL
SELECT source_event_hash, source_ts, event_ts, payload_event_name, payload_channel,
  'source_event_ambiguous_roster', 'row_quarantined'
FROM _news_usage_source
WHERE subject_match_count > 1 OR email_match_count > 1
UNION ALL
SELECT source_event_hash, source_ts, event_ts, payload_event_name, payload_channel,
  'source_event_identity_conflict', 'row_quarantined'
FROM _news_usage_source
WHERE subject_match_count = 1
  AND email_match_count = 1
  AND subject_roster_id != email_roster_id
UNION ALL
SELECT source_event_hash, source_ts, event_ts, payload_event_name, payload_channel,
  'conflicting_duplicate_event_id', 'row_quarantined'
FROM _news_usage_source WHERE minimum_content_hash != maximum_content_hash
UNION ALL
SELECT source.source_event_hash, source.source_ts, source.event_ts,
  source.payload_event_name, source.payload_channel,
  'conflicting_existing_event_id', 'row_quarantined'
FROM _news_usage_source source
WHERE NULLIF(source.event_id, '') IS NOT NULL
  AND EXISTS (
    SELECT 1
    FROM `${PROJECT_ID}.${DATASET_ID}.news_usage_events` existing
    WHERE existing.event_id = source.event_id
      AND existing.event_content_hash != source.event_content_hash
  )
UNION ALL
SELECT source_event_hash, source_ts, event_ts, payload_event_name, payload_channel,
  'duplicate_delivery_deduplicated', 'deduplicated'
FROM _news_usage_source
WHERE minimum_content_hash = maximum_content_hash AND delivery_row_number > 1
UNION ALL
SELECT source_event_hash, source_ts, event_ts, payload_event_name, payload_channel,
  'tab_view_context_invalid', 'row_quarantined'
FROM _news_usage_source
WHERE payload_event_name = 'tab_view'
  AND COALESCE(trigger, '') NOT IN ('initial', 'switch')
UNION ALL
SELECT source_event_hash, source_ts, event_ts, payload_event_name, payload_channel,
  'filter_change_context_missing', 'row_quarantined'
FROM _news_usage_source
WHERE payload_event_name = 'filter_change'
  AND (filter_snapshot_present IS NOT TRUE OR ARRAY_LENGTH(changed_fields) = 0)
UNION ALL
SELECT source_event_hash, source_ts, event_ts, payload_event_name, payload_channel,
  'detail_view_context_missing', 'row_quarantined'
FROM _news_usage_source
WHERE payload_event_name = 'detail_view'
  AND (content_event_id IS NULL OR surface IS NULL)
UNION ALL
SELECT source_event_hash, source_ts, event_ts, payload_event_name, payload_channel,
  'outbound_click_context_missing', 'row_quarantined'
FROM _news_usage_source
WHERE payload_event_name = 'outbound_click'
  AND (content_event_id IS NULL OR surface IS NULL OR link_kind IS NULL)
UNION ALL
SELECT source_event_hash, source_ts, event_ts, payload_event_name, payload_channel,
  'export_started_context_missing', 'row_quarantined'
FROM _news_usage_source
WHERE payload_event_name = 'export_started'
  AND (operation_id IS NULL OR filter_snapshot_present IS NOT TRUE)
UNION ALL
SELECT source_event_hash, source_ts, event_ts, payload_event_name, payload_channel,
  'export_finished_context_missing', 'row_quarantined'
FROM _news_usage_source
WHERE payload_event_name = 'export_finished'
  AND (operation_id IS NULL OR result IS NULL)
UNION ALL
SELECT source_event_hash, source_ts, event_ts, payload_event_name, payload_channel,
  'summary_view_context_invalid', 'row_quarantined'
FROM _news_usage_source
WHERE payload_event_name = 'summary_view'
  AND (
    COALESCE(payload_channel, '') != 'news'
    OR COALESCE(trigger, '') NOT IN ('manual', 'auto')
  )
UNION ALL
SELECT source_event_hash, source_ts, event_ts, payload_event_name, payload_channel,
  'export_terminal_duplicate', 'row_quarantined'
FROM _news_usage_source
WHERE payload_event_name = 'export_finished'
  AND export_terminal_row_number > 1
UNION ALL
SELECT source.source_event_hash, source.source_ts, source.event_ts,
  source.payload_event_name, source.payload_channel,
  'export_terminal_already_recorded', 'row_quarantined'
FROM _news_usage_source source
WHERE source.payload_event_name = 'export_finished'
  AND source.operation_id IS NOT NULL
  AND EXISTS (
    SELECT 1
    FROM `${PROJECT_ID}.${DATASET_ID}.news_usage_events` existing
    WHERE existing.event_name = 'export_finished'
      AND existing.user_id = source.user_id
      AND existing.channel = source.payload_channel
      AND existing.operation_id = source.operation_id
      AND existing.event_id != source.event_id
  )
UNION ALL
SELECT source.source_event_hash, source.source_ts, source.event_ts,
  source.payload_event_name, source.payload_channel,
  CASE issue
    WHEN 'content_event_id' THEN 'optional_content_event_id_omitted'
    WHEN 'content_event_version' THEN 'optional_content_event_version_omitted'
    WHEN 'content_event_type' THEN 'optional_content_event_type_omitted'
    WHEN 'content_domain_key' THEN 'optional_content_domain_key_omitted'
    WHEN 'content_geography_scope' THEN 'optional_content_geography_scope_omitted'
    WHEN 'content_source_id' THEN 'optional_content_source_id_omitted'
    WHEN 'content_category_key' THEN 'optional_content_category_key_omitted'
    WHEN 'source_catalog_version' THEN 'optional_source_catalog_version_omitted'
    WHEN 'filter_snapshot.domain_keys' THEN 'optional_filter_domain_keys_omitted'
    WHEN 'filter_snapshot.source_ids' THEN 'optional_filter_source_ids_omitted'
    WHEN 'filter_snapshot.category_keys' THEN 'optional_filter_category_keys_omitted'
    WHEN 'filter_snapshot.event_types' THEN 'optional_filter_event_types_omitted'
    WHEN 'filter_snapshot.news_geography_scope' THEN 'optional_filter_geo_omitted'
    WHEN 'filter_snapshot.start_date' THEN 'optional_filter_start_date_omitted'
    WHEN 'filter_snapshot.end_date' THEN 'optional_filter_end_date_omitted'
    WHEN 'filter_snapshot.has_query' THEN 'optional_filter_has_query_omitted'
    WHEN 'changed_fields' THEN 'optional_changed_fields_omitted'
    WHEN 'surface' THEN 'optional_surface_omitted'
    WHEN 'trigger' THEN 'optional_trigger_omitted'
    WHEN 'link_kind' THEN 'optional_link_kind_omitted'
    WHEN 'operation_id' THEN 'optional_operation_id_omitted'
    WHEN 'result' THEN 'optional_result_omitted'
    WHEN 'error_code' THEN 'optional_error_code_omitted'
    WHEN 'summary_date_jst' THEN 'optional_summary_date_omitted'
    ELSE 'optional_metadata_omitted'
  END,
  'axis_omitted'
FROM _news_usage_source source
CROSS JOIN UNNEST(source.producer_metadata_issues) issue;

CREATE TEMP TABLE _news_usage_admissible AS
SELECT source.*
FROM _news_usage_source source
WHERE source.delivery_row_number = 1
  AND NOT EXISTS (
    SELECT 1
    FROM _news_usage_issues issue
    WHERE issue.source_event_hash = source.source_event_hash
      AND issue.disposition = 'row_quarantined'
  );

UPDATE `${PROJECT_ID}.${DATASET_ID}.news_usage_event_issues` target
SET resolution_status = 'resolved',
    resolved_at = CURRENT_TIMESTAMP(),
    last_run_id = @run_id,
    last_observed_at = CURRENT_TIMESTAMP()
WHERE target.resolution_status = 'open'
  AND target.source_event_hash IN (
    SELECT DISTINCT source_event_hash FROM _news_usage_source
  )
  AND NOT EXISTS (
    SELECT 1 FROM _news_usage_issues issue
    WHERE issue.source_event_hash = target.source_event_hash
      AND issue.issue_code = target.issue_code
      AND issue.disposition = 'row_quarantined'
  );

MERGE `${PROJECT_ID}.${DATASET_ID}.news_usage_event_issues` target
USING (
  SELECT DISTINCT
    source_event_hash,
    issue_code,
    disposition,
    IF(
      payload_event_name IN (
        'tab_view', 'filter_change', 'detail_view', 'outbound_click',
        'export_started', 'export_finished', 'summary_view'
      ),
      payload_event_name,
      'unsupported'
    ) AS event_name,
    IF(payload_channel IN ('news', 'society'), payload_channel, 'unsupported')
      AS channel,
    source_ts,
    event_ts
  FROM _news_usage_issues
) source
ON target.source_event_hash = source.source_event_hash
  AND target.issue_code = source.issue_code
WHEN MATCHED THEN UPDATE SET
  disposition = source.disposition,
  event_name = source.event_name,
  channel = source.channel,
  source_ts = source.source_ts,
  event_ts = source.event_ts,
  last_run_id = @run_id,
  last_observed_at = CURRENT_TIMESTAMP(),
  observation_count = IF(
    target.last_run_id = @run_id,
    target.observation_count,
    target.observation_count + 1
  ),
  resolution_status = IF(
    source.disposition = 'row_quarantined', 'open', 'handled'
  ),
  resolved_at = IF(
    source.disposition = 'row_quarantined', NULL, CURRENT_TIMESTAMP()
  )
WHEN NOT MATCHED THEN INSERT (
  source_event_hash, issue_code, disposition, event_name, channel,
  source_ts, event_ts, first_run_id, last_run_id, first_observed_at,
  last_observed_at, observation_count, resolution_status, resolved_at
) VALUES (
  source.source_event_hash, source.issue_code, source.disposition,
  source.event_name, source.channel, source.source_ts, source.event_ts,
  @run_id, @run_id, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP(), 1,
  IF(source.disposition = 'row_quarantined', 'open', 'handled'),
  IF(source.disposition = 'row_quarantined', NULL, CURRENT_TIMESTAMP())
);

DELETE FROM `${PROJECT_ID}.${DATASET_ID}.pipeline_run_event_manifest`
WHERE run_id = @run_id
  AND DATE(observed_at) BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL 2 DAY)
    AND CURRENT_DATE();

INSERT INTO `${PROJECT_ID}.${DATASET_ID}.pipeline_run_event_manifest` (
  run_id, source_event_hash, event_key_hash, event_family, source_ts,
  event_ts, disposition, observed_at
)
SELECT
  @run_id,
  source.source_event_hash,
  IF(
    NULLIF(source.event_id, '') IS NULL,
    NULL,
    TO_HEX(SHA256(source.event_id))
  ),
  'news_usage',
  source.source_ts,
  source.event_ts,
  CASE
    WHEN EXISTS (
      SELECT 1 FROM _news_usage_issues issue
      WHERE issue.source_event_hash = source.source_event_hash
        AND issue.disposition = 'row_quarantined'
    ) THEN 'row_quarantined'
    WHEN source.delivery_row_number > 1 THEN 'deduplicated'
    ELSE 'canonical'
  END,
  CURRENT_TIMESTAMP()
FROM _news_usage_source source;

MERGE `${PROJECT_ID}.${DATASET_ID}.news_usage_events` target
USING (
  SELECT *
  FROM _news_usage_admissible
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY event_id ORDER BY source_ts DESC, insert_id DESC
  ) = 1
) source
ON target.event_id = source.event_id
WHEN MATCHED AND target.event_content_hash = source.event_content_hash THEN UPDATE SET
  actor_email_hash = COALESCE(
    target.actor_email_hash, source.normalized_actor_email_hash
  ),
  producer_revision = source.revision_name,
  producer_git_sha = source.git_sha,
  producer_build_id = source.build_id,
  source_ts = GREATEST(target.source_ts, source.source_ts),
  last_run_id = @run_id,
  last_seen_at = CURRENT_TIMESTAMP(),
  materialized_at = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (
  event_id, event_content_hash, usage_event_id, page_view_id, event_name,
  channel, occurred_at, usage_date_jst, user_id, actor_email_hash,
  ingested_roster_id,
  ingested_roster_snapshot_run_id, content_event_id, content_event_version,
  content_event_type,
  content_domain_key, content_geography_scope, content_source_id,
  content_category_key, source_catalog_version, filter_snapshot_present,
  filter_domain_keys, filter_source_ids, filter_category_keys,
  filter_event_types, filter_news_geography_scope, filter_start_date,
  filter_end_date, filter_has_query, changed_fields, surface, trigger,
  link_kind, operation_id, result, error_code, summary_date_jst,
  producer_revision, producer_git_sha, producer_build_id, source_service,
  source_ts, first_run_id, last_run_id, first_seen_at, last_seen_at,
  materialized_at
) VALUES (
  source.event_id, source.event_content_hash, LOWER(source.usage_event_id),
  LOWER(source.page_view_id), source.payload_event_name, source.payload_channel,
  source.occurred_at, DATE(source.occurred_at, '${MONITOR_TIMEZONE}'),
  source.user_id, source.normalized_actor_email_hash, source.roster_id,
  @roster_snapshot_run_id,
  source.content_event_id, source.content_event_version,
  source.content_event_type,
  source.content_domain_key, source.content_geography_scope,
  source.content_source_id, source.content_category_key,
  source.source_catalog_version, source.filter_snapshot_present,
  source.filter_domain_keys, source.filter_source_ids,
  source.filter_category_keys, source.filter_event_types,
  source.filter_news_geography_scope, source.filter_start_date,
  source.filter_end_date, source.filter_has_query, source.changed_fields,
  source.surface, source.trigger, source.link_kind, source.operation_id,
  source.result, source.error_code, source.summary_date_jst,
  source.revision_name, source.git_sha, source.build_id, source.source_service,
  source.source_ts, @run_id, @run_id, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP(),
  CURRENT_TIMESTAMP()
);

UPDATE `${PROJECT_ID}.${DATASET_ID}.pipeline_state`
SET data_through = @window_end,
    published_run_id = @run_id,
    roster_snapshot_run_id = @roster_snapshot_run_id,
    measurement_start_at = @measurement_start,
    source_service = @source_service,
    scope_policy_version = @scope_policy_version,
    global_roster_fingerprint = @global_roster_fingerprint,
    global_content_fingerprint = @global_content_fingerprint,
    user_map_roster_fingerprint = @user_map_roster_fingerprint,
    user_map_content_fingerprint = @user_map_content_fingerprint,
    status = 'succeeded',
    updated_at = CURRENT_TIMESTAMP(),
    lease_run_id = NULL,
    lease_acquired_at = NULL,
    lease_expires_at = NULL
WHERE source = 'news_usage'
  AND lease_run_id = @lease_id
  AND source_service = @source_service
  AND measurement_start_at = @measurement_start
  AND (
    (@expected_watermark IS NULL AND data_through IS NULL)
    OR data_through = @expected_watermark
  );
ASSERT @@row_count = 1 AS 'news usage watermark compare-and-set failed';

UPDATE `${PROJECT_ID}.${DATASET_ID}.pipeline_runs`
SET status = 'succeeded',
    finished_at = CURRENT_TIMESTAMP(),
    input_rows = (SELECT COUNT(*) FROM _news_usage_source),
    merged_rows = (SELECT COUNT(*) FROM _news_usage_admissible),
    duplicate_rows = (
      SELECT COUNT(*) FROM _news_usage_issues
      WHERE issue_code IN (
        'duplicate_delivery_deduplicated',
        'conflicting_duplicate_event_id',
        'conflicting_existing_event_id',
        'export_terminal_duplicate',
        'export_terminal_already_recorded'
      )
    )
WHERE run_id = @run_id
  AND source = 'news_usage'
  AND status = 'running'
  AND DATE(started_at) BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL 2 DAY)
    AND CURRENT_DATE();
ASSERT @@row_count = 1 AS 'news usage pipeline run row is missing';

COMMIT TRANSACTION;

SELECT
  (SELECT COUNT(*) FROM _news_usage_source) AS input_rows,
  (SELECT COUNT(*) FROM _news_usage_admissible) AS canonical_rows,
  (
    SELECT COUNT(DISTINCT source_event_hash)
    FROM _news_usage_issues
    WHERE disposition = 'row_quarantined'
  ) AS quarantined_rows;
