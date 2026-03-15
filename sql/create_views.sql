CREATE OR REPLACE VIEW `__PROJECT_ID__.__DATASET_ID__.v_requests` AS
SELECT
  timestamp AS ts,
  SAFE_CAST(httpRequest.status AS INT64) AS status,
  CAST(httpRequest.requestMethod AS STRING) AS method,
  CAST(httpRequest.requestUrl AS STRING) AS request_url,
  SAFE_CAST(REGEXP_EXTRACT(CAST(httpRequest.latency AS STRING), r'([0-9.]+)') AS FLOAT64) * 1000.0 AS latency_ms,
  LOWER(CAST(httpRequest.userAgent AS STRING)) AS user_agent,
  CASE
    WHEN REGEXP_CONTAINS(LOWER(CAST(httpRequest.userAgent AS STRING)), r'(iphone|android|mobile|ipad)') THEN 'mobile'
    WHEN CAST(httpRequest.userAgent AS STRING) IS NULL OR CAST(httpRequest.userAgent AS STRING) = '' THEN 'unknown'
    ELSE 'desktop'
  END AS device_class,
  resource.labels.service_name AS service_name
FROM `__PROJECT_ID__.__DATASET_ID__.run_googleapis_com_requests`
WHERE resource.type = 'cloud_run_revision'
  AND resource.labels.service_name = '__SERVICE_NAME__';

CREATE OR REPLACE VIEW `__PROJECT_ID__.__DATASET_ID__.v_query_suggest_results` AS
SELECT
  timestamp AS ts,
  REGEXP_EXTRACT(CAST(textPayload AS STRING), r"mode=([^ ]+)") AS mode,
  REGEXP_EXTRACT(CAST(textPayload AS STRING), r"conversation_id=([^ ]+)") AS conversation_id,
  REGEXP_EXTRACT(CAST(textPayload AS STRING), r"stage=([^ ]+)") AS stage,
  REGEXP_EXTRACT(CAST(textPayload AS STRING), r"stable=([^ ]+)") AS stable,
  CAST(REGEXP_EXTRACT(CAST(textPayload AS STRING), r"latency_ms=([0-9]+)") AS INT64) AS latency_ms,
  CAST(REGEXP_EXTRACT(CAST(textPayload AS STRING), r"suggestion_count=([0-9]+)") AS INT64) AS suggestion_count
FROM `__PROJECT_ID__.__DATASET_ID__.run_googleapis_com_stdout`
WHERE resource.type = 'cloud_run_revision'
  AND resource.labels.service_name = '__SERVICE_NAME__'
  AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^query_suggest_result ");

CREATE OR REPLACE VIEW `__PROJECT_ID__.__DATASET_ID__.v_query_suggest_degraded` AS
SELECT
  timestamp AS ts,
  REGEXP_EXTRACT(CAST(textPayload AS STRING), r"reason=([^ ]+)") AS reason,
  REGEXP_EXTRACT(CAST(textPayload AS STRING), r"fallback=([^ ]+)") AS fallback_source,
  REGEXP_EXTRACT(CAST(textPayload AS STRING), r"conversation_id=([^ ]+)") AS conversation_id
FROM `__PROJECT_ID__.__DATASET_ID__.run_googleapis_com_stdout`
WHERE resource.type = 'cloud_run_revision'
  AND resource.labels.service_name = '__SERVICE_NAME__'
  AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^query_suggest_refine_degraded ");

CREATE OR REPLACE VIEW `__PROJECT_ID__.__DATASET_ID__.v_sync_telemetry` AS
SELECT
  timestamp AS ts,
  REGEXP_EXTRACT(CAST(textPayload AS STRING), r"event=([^ ]+)") AS event,
  REGEXP_EXTRACT(CAST(textPayload AS STRING), r"conversation_id=([^ ]+)") AS conversation_id,
  REGEXP_EXTRACT(CAST(textPayload AS STRING), r"detail=(\{.*\})$") AS detail_json
FROM `__PROJECT_ID__.__DATASET_ID__.run_googleapis_com_stdout`
WHERE resource.type = 'cloud_run_revision'
  AND resource.labels.service_name = '__SERVICE_NAME__'
  AND REGEXP_CONTAINS(CAST(textPayload AS STRING), r"^chat_sync_telemetry ");
