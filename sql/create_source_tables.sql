-- Cloud Logging owns the two raw destination tables. TO_JSON_STRING keeps this
-- view publishable before and after the stdout schema first gains jsonPayload:
-- no query references a field that may not exist in the current raw schema.
CREATE OR REPLACE VIEW `${PROJECT_ID}.${DATASET_ID}.monitor_event_source` AS
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
  WHERE JSON_VALUE(raw_json, '$.resource.labels.service_name') = '${SOURCE_SERVICE}'
)
SELECT
  source_ts,
  JSON_VALUE(raw_json, '$.insertId') AS insert_id,
  JSON_VALUE(event_payload, '$.event_id') AS event_id,
  JSON_VALUE(event_payload, '$.event_family') AS event_family,
  SAFE_CAST(JSON_VALUE(event_payload, '$.event_ts') AS TIMESTAMP) AS event_ts,
  JSON_VALUE(event_payload, '$.trace_id') AS trace_id,
  JSON_VALUE(event_payload, '$.request_id') AS request_id,
  JSON_VALUE(event_payload, '$.conversation_id') AS conversation_id,
  JSON_VALUE(event_payload, '$.turn_id') AS turn_id,
  JSON_VALUE(event_payload, '$.message_id') AS message_id,
  JSON_VALUE(event_payload, '$.user_id') AS user_id,
  JSON_VALUE(event_payload, '$.mode') AS mode,
  JSON_VALUE(event_payload, '$.device_class') AS device_class,
  JSON_VALUE(event_payload, '$.endpoint_class') AS endpoint_class,
  JSON_VALUE(event_payload, '$.revision_name') AS revision_name,
  JSON_VALUE(event_payload, '$.git_sha') AS git_sha,
  JSON_VALUE(event_payload, '$.build_id') AS build_id,
  JSON_VALUE(event_payload, '$.payload_json') AS payload_json
FROM normalized
WHERE JSON_VALUE(event_payload, '$.monitor_event') = 'true';

CREATE OR REPLACE VIEW `${PROJECT_ID}.${DATASET_ID}.http_request_source` AS
WITH serialized AS (
  SELECT timestamp AS source_ts, TO_JSON_STRING(raw) AS raw_json
  FROM `${PROJECT_ID}.${DATASET_ID}.run_googleapis_com_requests` raw
  WHERE timestamp IS NOT NULL
)
SELECT
  source_ts,
  JSON_VALUE(raw_json, '$.insertId') AS insert_id,
  JSON_VALUE(raw_json, '$.httpRequest.requestMethod') AS method,
  JSON_VALUE(raw_json, '$.httpRequest.requestUrl') AS request_url,
  SAFE_CAST(JSON_VALUE(raw_json, '$.httpRequest.status') AS INT64) AS status,
  JSON_VALUE(raw_json, '$.httpRequest.latency') AS latency_text,
  JSON_VALUE(raw_json, '$.resource.labels.revision_name') AS revision_name
FROM serialized
WHERE JSON_VALUE(raw_json, '$.resource.labels.service_name') = '${SOURCE_SERVICE}';
