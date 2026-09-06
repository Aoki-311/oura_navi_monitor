-- Independent source view for the authenticated News/Society usage envelope.
-- The Logging resource owns environment identity.  The payload cannot select
-- another service. The Chat source excludes this separately owned family.
CREATE OR REPLACE VIEW `${PROJECT_ID}.${DATASET_ID}.news_usage_event_source` AS
WITH serialized AS (
  SELECT
    timestamp AS source_ts,
    TO_JSON_STRING(raw) AS raw_json
  FROM `${PROJECT_ID}.${DATASET_ID}.run_googleapis_com_stdout` raw
  WHERE timestamp IS NOT NULL
), normalized AS (
  SELECT
    source_ts,
    raw_json,
    COALESCE(
      SAFE.PARSE_JSON(JSON_QUERY(raw_json, '$.jsonPayload')),
      SAFE.PARSE_JSON(JSON_VALUE(raw_json, '$.textPayload'))
    ) AS event_payload
  FROM serialized
  WHERE JSON_VALUE(raw_json, '$.resource.labels.service_name')
    = '${NEWS_USAGE_SOURCE_SERVICE}'
)
SELECT
  source_ts,
  JSON_VALUE(raw_json, '$.insertId') AS insert_id,
  JSON_VALUE(event_payload, '$.event_id') AS event_id,
  JSON_VALUE(event_payload, '$.event_family') AS event_family,
  JSON_VALUE(event_payload, '$.monitor_contract_version')
    AS monitor_contract_version,
  SAFE_CAST(JSON_VALUE(event_payload, '$.event_ts') AS TIMESTAMP) AS event_ts,
  JSON_VALUE(event_payload, '$.user_id') AS user_id,
  JSON_VALUE(event_payload, '$.actor_email_hash') AS actor_email_hash,
  SAFE_CAST(JSON_VALUE(event_payload, '$.received_at') AS TIMESTAMP) AS received_at,
  JSON_VALUE(raw_json, '$.resource.labels.service_name') AS source_service,
  JSON_VALUE(event_payload, '$.service_name') AS claimed_service_name,
  COALESCE(
    NULLIF(JSON_VALUE(raw_json, '$.resource.labels.revision_name'), ''),
    NULLIF(JSON_VALUE(event_payload, '$.revision_name'), '')
  ) AS revision_name,
  JSON_VALUE(event_payload, '$.git_sha') AS git_sha,
  JSON_VALUE(event_payload, '$.build_id') AS build_id,
  JSON_QUERY(event_payload, '$.metadata_issues') AS metadata_issues_json,
  JSON_VALUE(event_payload, '$.payload_json') AS payload_json
FROM normalized
WHERE JSON_VALUE(event_payload, '$.monitor_event') = 'true'
  AND JSON_VALUE(event_payload, '$.event_family') = 'news_usage';
