-- One partition-bounded semantic owner for both the overview and user detail.
-- The answer CTE guarantees at most one answer row per request, so a retry or
-- duplicate terminal event can never multiply a user's question count.
CREATE OR REPLACE TABLE FUNCTION `${PROJECT_ID}.${DATASET_ID}.dashboard_events_v2`(
  p_start_date DATE,
  p_end_date DATE,
  p_run_id STRING
) AS (
  WITH canonical_answers AS (
    SELECT *
    FROM `${PROJECT_ID}.${DATASET_ID}.answer_events`
    WHERE answer_date BETWEEN DATE_SUB(p_start_date, INTERVAL 1 DAY)
      AND DATE_ADD(p_end_date, INTERVAL 1 DAY)
    QUALIFY ROW_NUMBER() OVER (
      PARTITION BY COALESCE(NULLIF(request_id, ''), event_id)
      ORDER BY answer_ts DESC, source_event_ts DESC, event_id DESC
    ) = 1
  ), versioned_questions AS (
    SELECT
      q.*,
      CASE
        WHEN q.record_origin IN ('firestore_history', 'legacy_audit_history')
          THEN COALESCE(NULLIF(q.analytics_contract_version, ''), 'request_spec_analytics_v1')
        ELSE NULLIF(q.analytics_contract_version, '')
      END AS effective_analytics_contract_version,
      COALESCE(
        NULLIF(q.product_resolution_status, ''),
        CASE
          WHEN q.classification_status = 'producer_invalid' THEN 'producer_invalid'
          WHEN q.product_candidate_count IS NULL OR q.product_resolved_count IS NULL THEN NULL
          WHEN q.product_candidate_count = 0 THEN 'not_applicable'
          WHEN q.product_resolved_count = q.product_candidate_count THEN 'resolved'
          WHEN q.product_resolved_count = 0 THEN 'unresolved'
          ELSE 'partially_resolved'
        END
      ) AS effective_product_resolution_status
    FROM `${PROJECT_ID}.${DATASET_ID}.question_events` q
    WHERE q.question_date BETWEEN p_start_date AND p_end_date
      AND q.endpoint_class IN ('ask', 'ask_stream')
  ), classification_owned AS (
    SELECT
      q.*,
      CASE
        WHEN q.record_origin IN ('firestore_history', 'legacy_audit_history') THEN 'unmeasured'
        WHEN q.effective_analytics_contract_version = 'request_spec_analytics_v1'
          AND q.classification_status IN ('classified', 'unclassified') THEN 'measured'
        WHEN q.effective_analytics_contract_version = 'request_spec_analytics_v2'
          AND q.classification_status IN ('classified', 'unclassified')
          AND q.primary_question_category IN (
            'product_information', 'price_product_code', 'comparison_fit_selection',
            'usage_procedure', 'troubleshooting_safety', 'sales_proposal',
            'institution_gpo_market', 'document_search', 'other_general',
            'unclassified'
          )
          AND ARRAY_LENGTH(IFNULL(q.question_categories, [])) > 0
          AND q.primary_question_category IN UNNEST(IFNULL(q.question_categories, []))
          AND NOT EXISTS (
            SELECT 1
            FROM UNNEST(IFNULL(q.question_categories, [])) category
            WHERE category NOT IN (
              'product_information', 'price_product_code', 'comparison_fit_selection',
              'usage_procedure', 'troubleshooting_safety', 'sales_proposal',
              'institution_gpo_market', 'document_search', 'other_general',
              'unclassified'
            )
          )
          AND q.is_multi_intent = (ARRAY_LENGTH(q.question_categories) > 1)
          AND (
            (
              q.classification_status = 'classified'
              AND EXISTS (
                SELECT 1 FROM UNNEST(q.question_categories) category
                WHERE category != 'unclassified'
              )
            )
            OR (
              q.classification_status = 'unclassified'
              AND NOT EXISTS (
                SELECT 1 FROM UNNEST(q.question_categories) category
                WHERE category != 'unclassified'
              )
            )
          )
          AND NOT EXISTS (
            SELECT 1
            FROM UNNEST(IFNULL(q.classification_reason_codes, [])) reason
            WHERE reason IN ('invalid_question_category', 'request_spec_unavailable')
          ) THEN 'measured'
        ELSE 'unmeasured'
      END AS classification_measurement_state
    FROM versioned_questions q
  ), axis_owned_questions AS (
    SELECT
      q.*,
      CASE
        WHEN q.record_origin IN ('firestore_history', 'legacy_audit_history') THEN 'unmeasured'
        WHEN q.effective_analytics_contract_version = 'request_spec_analytics_v1'
          AND q.classification_measurement_state = 'measured'
          AND IFNULL(ARRAY_LENGTH(q.analytics_tasks), 0) > 0 THEN 'measured'
        WHEN q.effective_analytics_contract_version = 'request_spec_analytics_v2'
          AND IFNULL(ARRAY_LENGTH(q.analytics_tasks), 0) > 0
          AND NOT EXISTS (
            SELECT 1
            FROM UNNEST(IFNULL(q.analytics_tasks, [])) task
            WHERE task NOT IN (
              'fact_lookup', 'explanation', 'comparison_selection',
              'procedure_guidance', 'troubleshooting', 'content_creation',
              'source_retrieval', 'market_research', 'other', 'unclassified'
            )
          )
          AND NOT EXISTS (
            SELECT 1
            FROM UNNEST(IFNULL(q.classification_reason_codes, [])) reason
            WHERE reason IN ('invalid_analytics_task', 'request_spec_unavailable')
          ) THEN 'measured'
        ELSE 'unmeasured'
      END AS task_measurement_state,
      CASE
        WHEN q.record_origin IN ('firestore_history', 'legacy_audit_history') THEN 'unmeasured'
        WHEN q.product_candidate_count IS NULL OR q.product_resolved_count IS NULL
          OR q.product_candidate_count < 0 OR q.product_resolved_count < 0
          OR q.product_resolved_count > q.product_candidate_count THEN 'unmeasured'
        WHEN q.effective_analytics_contract_version = 'request_spec_analytics_v1'
          AND q.classification_status != 'producer_invalid' THEN 'measured'
        WHEN q.effective_analytics_contract_version = 'request_spec_analytics_v2'
          AND ARRAY_LENGTH(IFNULL(q.product_keys, []))
            = ARRAY_LENGTH(IFNULL(q.product_names, []))
          AND ARRAY_LENGTH(IFNULL(q.product_keys, [])) <= q.product_resolved_count
          AND (
            (
              NULLIF(q.primary_product_key, '') IS NULL
              AND NULLIF(q.primary_product_name, '') IS NULL
            )
            OR EXISTS (
              SELECT 1
              FROM UNNEST(IFNULL(q.product_keys, [])) product_key WITH OFFSET position
              WHERE product_key = q.primary_product_key
                AND q.product_names[SAFE_OFFSET(position)] = q.primary_product_name
            )
          )
          AND (
            (
              q.effective_product_resolution_status = 'not_applicable'
              AND q.product_candidate_count = 0
              AND q.product_resolved_count = 0
              AND ARRAY_LENGTH(IFNULL(q.product_keys, [])) = 0
              AND ARRAY_LENGTH(IFNULL(q.product_resolution_reason_codes, [])) = 0
            )
            OR (
              q.effective_product_resolution_status = 'resolved'
              AND q.product_candidate_count > 0
              AND q.product_resolved_count = q.product_candidate_count
              AND ARRAY_LENGTH(IFNULL(q.product_resolution_reason_codes, [])) = 0
            )
            OR (
              q.effective_product_resolution_status = 'partially_resolved'
              AND q.product_resolved_count > 0
              AND q.product_resolved_count < q.product_candidate_count
            )
            OR (
              q.effective_product_resolution_status = 'unresolved'
              AND q.product_candidate_count > 0
              AND q.product_resolved_count = 0
              AND ARRAY_LENGTH(IFNULL(q.product_keys, [])) = 0
            )
          ) THEN 'measured'
        ELSE 'unmeasured'
      END AS product_measurement_state
    FROM classification_owned q
  )
  SELECT
    q.event_id AS question_event_id,
    q.question_ts,
    q.question_date,
    q.roster_id,
    q.request_id,
    q.conversation_id,
    q.turn_id,
    scope.area_key,
    scope.area,
    scope.role,
    scope.department,
    q.valid_question,
    q.mode,
    q.device_class,
    q.primary_question_category,
    q.question_categories,
    q.classification_status,
    q.effective_analytics_contract_version AS analytics_contract_version,
    IFNULL(q.classification_reason_codes, []) AS classification_reason_codes,
    q.classification_measurement_state,
    q.is_multi_intent,
    q.analytics_tasks,
    q.task_measurement_state,
    q.primary_product_key,
    q.primary_product_name,
    q.product_keys,
    q.product_names,
    q.product_candidate_count,
    q.product_resolved_count,
    q.effective_product_resolution_status AS product_resolution_status,
    IFNULL(q.product_resolution_reason_codes, []) AS product_resolution_reason_codes,
    q.product_measurement_state,
    q.record_origin,
    q.measurement_profile AS question_measurement_profile,
    answer.measurement_available,
    answer.complete_delivery,
    answer.primary_failure_reason,
    answer.total_latency_ms,
    answer.measurement_profile AS answer_measurement_profile
  FROM axis_owned_questions q
  LEFT JOIN canonical_answers answer
    ON q.request_id IS NOT NULL
    AND q.request_id != ''
    AND q.request_id = answer.request_id
    AND answer.answer_date BETWEEN DATE_SUB(q.question_date, INTERVAL 1 DAY)
      AND DATE_ADD(q.question_date, INTERVAL 1 DAY)
  LEFT JOIN `${PROJECT_ID}.${DATASET_ID}.user_scope` scope
    ON q.roster_id = scope.roster_id
    AND scope.snapshot_run_id = p_run_id
);

-- The current USER_MAP roster reads the same question fact owner directly.
-- No user_daily copy is maintained, so a stale aggregate cannot disagree with
-- the overview or hide historical last-use timestamps.
CREATE OR REPLACE TABLE FUNCTION `${PROJECT_ID}.${DATASET_ID}.dashboard_user_list_v2`(
  p_history_start DATE,
  p_as_of TIMESTAMP,
  p_run_id STRING
) AS (
  WITH current_scope AS (
    SELECT *
    FROM `${PROJECT_ID}.${DATASET_ID}.user_scope`
    WHERE snapshot_run_id = p_run_id
      AND is_active = TRUE AND user_map_scope_enabled = TRUE
  ), question_facts AS (
    SELECT roster_id, question_date, question_ts
    FROM `${PROJECT_ID}.${DATASET_ID}.question_events`
    WHERE question_date BETWEEN p_history_start
      AND DATE(p_as_of, '${MONITOR_TIMEZONE}')
      AND question_ts < p_as_of
      AND valid_question = TRUE
      AND endpoint_class IN ('ask', 'ask_stream')
  ), metrics AS (
    SELECT
      roster_id,
      MAX(question_ts) AS last_active_at,
      COUNT(DISTINCT IF(question_date >= DATE_SUB(DATE(p_as_of, '${MONITOR_TIMEZONE}'), INTERVAL 6 DAY), question_date, NULL)) AS active_days_7,
      COUNTIF(question_date >= DATE_SUB(DATE(p_as_of, '${MONITOR_TIMEZONE}'), INTERVAL 6 DAY)) AS user_message_count_7
    FROM question_facts
    GROUP BY roster_id
  )
  SELECT
    scope.roster_id,
    scope.area_key,
    scope.area,
    scope.role,
    scope.department,
    metrics.last_active_at,
    COALESCE(metrics.active_days_7, 0) AS active_days_7,
    COALESCE(metrics.user_message_count_7, 0) AS user_message_count_7
  FROM current_scope scope
  LEFT JOIN metrics USING (roster_id)
);
