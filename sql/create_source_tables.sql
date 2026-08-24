-- Cloud Logging owns the two raw destination tables. These views expose only
-- privacy-bounded fields to the incremental job. Every consumer must add an
-- explicit @window_start/@window_end predicate on source_ts.
CREATE OR REPLACE VIEW `${PROJECT_ID}.${DATASET_ID}.monitor_event_source` AS
SELECT
  timestamp AS source_ts,
  insertId AS insert_id,
  CAST(jsonPayload.event_id AS STRING) AS event_id,
  CAST(jsonPayload.event_family AS STRING) AS event_family,
  SAFE_CAST(jsonPayload.event_ts AS TIMESTAMP) AS event_ts,
  CAST(jsonPayload.trace_id AS STRING) AS trace_id,
  CAST(jsonPayload.request_id AS STRING) AS request_id,
  CAST(jsonPayload.conversation_id AS STRING) AS conversation_id,
  CAST(jsonPayload.turn_id AS STRING) AS turn_id,
  CAST(jsonPayload.message_id AS STRING) AS message_id,
  CAST(jsonPayload.user_key AS STRING) AS user_key,
  CAST(jsonPayload.mode AS STRING) AS mode,
  CAST(jsonPayload.device_class AS STRING) AS device_class,
  CAST(jsonPayload.endpoint_class AS STRING) AS endpoint_class,
  CAST(jsonPayload.revision_name AS STRING) AS revision_name,
  CAST(jsonPayload.git_sha AS STRING) AS git_sha,
  CAST(jsonPayload.build_id AS STRING) AS build_id,
  CAST(jsonPayload.payload_json AS STRING) AS payload_json
FROM `${PROJECT_ID}.${DATASET_ID}.run_googleapis_com_stdout`
WHERE jsonPayload.monitor_event = TRUE;

CREATE OR REPLACE VIEW `${PROJECT_ID}.${DATASET_ID}.http_request_source` AS
SELECT
  timestamp AS source_ts,
  insertId AS insert_id,
  CAST(httpRequest.requestMethod AS STRING) AS method,
  CAST(httpRequest.requestUrl AS STRING) AS request_url,
  SAFE_CAST(httpRequest.status AS INT64) AS status,
  CAST(httpRequest.latency AS STRING) AS latency_text,
  CAST(resource.labels.revision_name AS STRING) AS revision_name
FROM `${PROJECT_ID}.${DATASET_ID}.run_googleapis_com_requests`;
