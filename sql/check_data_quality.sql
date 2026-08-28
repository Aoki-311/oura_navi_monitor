-- Required parameters: @window_start TIMESTAMP, @window_end TIMESTAMP.
-- Every check has an explicit disposition. Only batch_blocking can roll back a
-- publish; row_quarantined and axis_unmeasured remain visible without making
-- an unrelated valid event disappear. Row-level source dispositions are also
-- persisted by merge_incremental.sql in the privacy-safe hashed issue ledger.
WITH source_events AS (
  SELECT
    event_id,
    event_family,
    user_id,
    COALESCE(event_ts, source_ts) AS event_ts
  FROM _run_admissible_monitor_events
), source_questions AS (
  SELECT event_id, user_id, event_ts
  FROM source_events
  WHERE event_family = 'question_received'
), accepted_ask_requests AS (
  SELECT insert_id
  FROM `${PROJECT_ID}.${DATASET_ID}.http_request_source`
  WHERE source_ts >= @window_start AND source_ts < @window_end
    AND method = 'POST'
    AND status BETWEEN 200 AND 399
    AND REGEXP_CONTAINS(
      REGEXP_EXTRACT(request_url, r'^https?://[^/]+([^?]*)'),
      r'^/v[0-9]+/ask(/stream)?/?$'
    )
), source_answers AS (
  SELECT event_id, user_id
  FROM source_events
  WHERE event_family = 'answer_completed'
), questions AS (
  SELECT question.*
  FROM `${PROJECT_ID}.${DATASET_ID}.question_events` question
  JOIN _run_affected_request_ids affected USING (request_id)
  WHERE question.question_date BETWEEN
    DATE_SUB(event_partition_start, INTERVAL 1 DAY)
    AND DATE_ADD(event_partition_end, INTERVAL 1 DAY)
), answers AS (
  SELECT answer.*
  FROM `${PROJECT_ID}.${DATASET_ID}.answer_events` answer
  JOIN _run_affected_request_ids affected USING (request_id)
  WHERE answer.answer_date BETWEEN
    DATE_SUB(affected_answer_partition_start, INTERVAL 1 DAY)
    AND DATE_ADD(affected_answer_partition_end, INTERVAL 1 DAY)
), question_links AS (
  SELECT DISTINCT question.request_id
  FROM `${PROJECT_ID}.${DATASET_ID}.question_events` question
  JOIN _run_affected_request_ids affected USING (request_id)
  WHERE question.question_date BETWEEN
    DATE_SUB(event_partition_start, INTERVAL 1 DAY)
    AND DATE_ADD(event_partition_end, INTERVAL 1 DAY)
), checks AS (
  SELECT 'run_manifest_accounting_mismatch' AS check_name,
    ABS(
      (SELECT COUNT(*) FROM _run_monitor_source)
      - (
        SELECT COUNT(*)
        FROM `${PROJECT_ID}.${DATASET_ID}.pipeline_run_event_manifest`
        WHERE DATE(observed_at) BETWEEN
          DATE_SUB(CURRENT_DATE(), INTERVAL 2 DAY) AND CURRENT_DATE()
          AND run_id = @run_id
      )
    ) AS failure_count
  UNION ALL
  SELECT 'repaired_duplicate_question_event_id' AS check_name,
    COUNTIF(fact_table = 'question_events') AS failure_count
  FROM _run_duplicate_fact_keys
  UNION ALL
  SELECT 'repaired_duplicate_answer_event_id',
    COUNTIF(fact_table = 'answer_events')
  FROM _run_duplicate_fact_keys
  UNION ALL
  SELECT 'duplicate_question_event_id' AS check_name, COUNT(*) - COUNT(DISTINCT event_id) AS failure_count FROM questions
  UNION ALL
  SELECT 'duplicate_answer_event_id', COUNT(*) - COUNT(DISTINCT event_id) FROM answers
  UNION ALL
  SELECT 'duplicate_answer_request_id', COUNT(*) - COUNT(DISTINCT request_id) FROM answers
  WHERE request_id IS NOT NULL AND request_id != ''
  UNION ALL
  SELECT 'duplicate_source_question_event_id', COUNTIF(
    issue_code = 'conflicting_duplicate_event_id'
    AND event_family = 'question_received'
  ) FROM _run_event_issues
  UNION ALL
  SELECT 'duplicate_source_answer_event_id', COUNTIF(
    issue_code = 'conflicting_duplicate_event_id'
    AND event_family = 'answer_completed'
  ) FROM _run_event_issues
  UNION ALL
  SELECT 'source_event_missing_identity', COUNTIF(issue_code IN (
    'source_event_missing_event_id', 'source_event_missing_user_id'
  )) FROM _run_event_issues
  UNION ALL
  SELECT 'unsupported_event_family', COUNTIF(
    issue_code = 'unsupported_event_family'
  ) FROM _run_event_issues
  UNION ALL
  SELECT 'source_event_missing_correlation', COUNTIF(
    issue_code = 'source_event_missing_correlation'
  ) FROM _run_event_issues
  UNION ALL
  SELECT 'source_event_invalid_timestamp', COUNTIF(issue_code IN (
    'source_event_timestamp_before_analytics_start',
    'source_event_timestamp_in_future',
    'invalid_persistence_answer_ts'
  )) FROM _run_event_issues
  UNION ALL
  SELECT 'source_event_ambiguous_roster', COUNTIF(
    issue_code = 'source_event_ambiguous_roster'
  ) FROM _run_event_issues
  UNION ALL
  SELECT 'duplicate_delivery_deduplicated', COUNTIF(
    issue_code = 'duplicate_delivery_deduplicated'
  ) FROM _run_event_issues
  UNION ALL
  SELECT 'accepted_http_without_question_event', GREATEST(
    (SELECT COUNT(*) FROM accepted_ask_requests)
      - (SELECT COUNT(DISTINCT event_id) FROM source_questions),
    0
  )
  UNION ALL
  SELECT 'unknown_question_category', COUNTIF(primary_question_category IS NULL OR primary_question_category NOT IN (
    'product_information','price_product_code','comparison_fit_selection','usage_procedure',
    'troubleshooting_safety','sales_proposal','institution_gpo_market','document_search',
    'other_general','unclassified'
  )) FROM questions
  UNION ALL
  SELECT 'unknown_secondary_question_category', COUNT(*)
  FROM questions, UNNEST(IFNULL(question_categories, [])) category
  WHERE category NOT IN (
    'product_information','price_product_code','comparison_fit_selection','usage_procedure',
    'troubleshooting_safety','sales_proposal','institution_gpo_market','document_search',
    'other_general','unclassified'
  )
  UNION ALL
  SELECT 'unknown_analytics_task', COUNT(*)
  FROM questions, UNNEST(IFNULL(analytics_tasks, [])) task
  WHERE task NOT IN (
    'fact_lookup','explanation','comparison_selection','procedure_guidance',
    'troubleshooting','content_creation','source_retrieval','market_research',
    'other','unclassified'
  )
  UNION ALL
  SELECT 'missing_analytics_axes', COUNTIF(
    IFNULL(ARRAY_LENGTH(question_categories), 0) = 0
    OR (
      IFNULL(record_origin, '') NOT IN ('firestore_history', 'legacy_audit_history')
      AND IFNULL(ARRAY_LENGTH(analytics_tasks), 0) = 0
    )
  ) FROM questions
  UNION ALL
  SELECT 'invalid_classification_producer', COUNTIF(classification_status = 'producer_invalid') FROM questions
  UNION ALL
  SELECT 'invalid_classification_semantics', COUNT(*)
  FROM questions q
  WHERE q.analytics_contract_version = 'request_spec_analytics_v2'
    AND NOT COALESCE((
      ARRAY_LENGTH(IFNULL(q.question_categories, [])) > 0
      AND q.primary_question_category IN UNNEST(IFNULL(q.question_categories, []))
      AND q.is_multi_intent = (ARRAY_LENGTH(q.question_categories) > 1)
      AND (
        (
          q.classification_status = 'classified'
          AND EXISTS (
            SELECT 1 FROM UNNEST(q.question_categories) category
            WHERE category != 'unclassified'
          )
          AND NOT EXISTS (
            SELECT 1 FROM UNNEST(IFNULL(q.classification_reason_codes, [])) reason
            WHERE reason IN ('invalid_question_category', 'request_spec_unavailable')
          )
        )
        OR (
          q.classification_status = 'unclassified'
          AND NOT EXISTS (
            SELECT 1 FROM UNNEST(q.question_categories) category
            WHERE category != 'unclassified'
          )
          AND NOT EXISTS (
            SELECT 1 FROM UNNEST(IFNULL(q.classification_reason_codes, [])) reason
            WHERE reason IN ('invalid_question_category', 'request_spec_unavailable')
          )
        )
        OR (
          q.classification_status = 'producer_invalid'
          AND EXISTS (
            SELECT 1 FROM UNNEST(IFNULL(q.classification_reason_codes, [])) reason
            WHERE reason IN ('invalid_question_category', 'request_spec_unavailable')
          )
        )
      )
    ), FALSE)
  UNION ALL
  SELECT 'invalid_task_semantics', COUNT(*)
  FROM questions q
  WHERE q.analytics_contract_version = 'request_spec_analytics_v2'
    AND (
      ARRAY_LENGTH(IFNULL(q.analytics_tasks, [])) = 0
      OR (
        EXISTS (
          SELECT 1 FROM UNNEST(IFNULL(q.classification_reason_codes, [])) reason
          WHERE reason IN ('invalid_analytics_task', 'request_spec_unavailable')
        )
        AND 'unclassified' NOT IN UNNEST(IFNULL(q.analytics_tasks, []))
      )
    )
  UNION ALL
  SELECT 'invalid_product_resolution_counts', COUNTIF(
    product_candidate_count IS NULL OR product_resolved_count IS NULL
    OR product_candidate_count < 0 OR product_resolved_count < 0
    OR product_resolved_count > product_candidate_count
  ) FROM questions
  UNION ALL
  SELECT 'invalid_product_resolution_semantics', COUNT(*)
  FROM questions q
  WHERE q.analytics_contract_version = 'request_spec_analytics_v2'
    AND NOT COALESCE((
      (
        q.product_resolution_status = 'not_applicable'
        AND q.product_candidate_count = 0
        AND q.product_resolved_count = 0
        AND ARRAY_LENGTH(IFNULL(q.product_keys, [])) = 0
        AND ARRAY_LENGTH(IFNULL(q.product_resolution_reason_codes, [])) = 0
      )
      OR (
        q.product_resolution_status = 'resolved'
        AND q.product_candidate_count > 0
        AND q.product_resolved_count = q.product_candidate_count
        AND ARRAY_LENGTH(IFNULL(q.product_resolution_reason_codes, [])) = 0
      )
      OR (
        q.product_resolution_status = 'partially_resolved'
        AND q.product_resolved_count > 0
        AND q.product_resolved_count < q.product_candidate_count
      )
      OR (
        q.product_resolution_status = 'unresolved'
        AND q.product_candidate_count > 0
        AND q.product_resolved_count = 0
        AND ARRAY_LENGTH(IFNULL(q.product_keys, [])) = 0
      )
      OR (
        q.product_resolution_status = 'producer_invalid'
        AND EXISTS (
          SELECT 1
          FROM UNNEST(IFNULL(q.product_resolution_reason_codes, [])) reason
          WHERE reason IN (
            'invalid_product_subject_indexes', 'product_subject_not_in_demand',
            'product_subject_not_found', 'resolved_identity_incomplete',
            'request_spec_unavailable'
          )
        )
      )
      OR (
        q.product_resolution_status = 'resolver_failed'
        AND 'product_resolver_failed' IN UNNEST(
          IFNULL(q.product_resolution_reason_codes, [])
        )
      )
    ), FALSE)
  UNION ALL
  SELECT 'invalid_product_identity_alignment', COUNT(*)
  FROM questions q
  WHERE q.analytics_contract_version = 'request_spec_analytics_v2'
    AND (
      ARRAY_LENGTH(IFNULL(q.product_keys, []))
        != ARRAY_LENGTH(IFNULL(q.product_names, []))
      OR ARRAY_LENGTH(IFNULL(q.product_keys, [])) > q.product_resolved_count
      OR (
        (NULLIF(q.primary_product_key, '') IS NULL)
        != (NULLIF(q.primary_product_name, '') IS NULL)
      )
      OR (
        NULLIF(q.primary_product_key, '') IS NOT NULL
        AND NOT EXISTS (
          SELECT 1
          FROM UNNEST(IFNULL(q.product_keys, [])) product_key WITH OFFSET position
          WHERE product_key = q.primary_product_key
            AND q.product_names[SAFE_OFFSET(position)] = q.primary_product_name
        )
      )
    )
  UNION ALL
  SELECT 'unknown_analytics_contract_version', COUNTIF(
    analytics_contract_version IS NOT NULL
    AND analytics_contract_version NOT IN ('request_spec_analytics_v1', 'request_spec_analytics_v2')
  ) FROM questions
  UNION ALL
  SELECT 'unknown_classification_reason_code', COUNT(*)
  FROM questions, UNNEST(IFNULL(classification_reason_codes, [])) reason
  WHERE reason NOT IN (
    'invalid_question_category','invalid_analytics_task','request_spec_unavailable'
  )
  UNION ALL
  SELECT 'unknown_product_resolution_status', COUNTIF(
    analytics_contract_version = 'request_spec_analytics_v2'
    AND IFNULL(product_resolution_status, '') NOT IN (
      'not_applicable','resolved','partially_resolved','unresolved',
      'producer_invalid','resolver_failed'
    )
  ) FROM questions
  UNION ALL
  SELECT 'unknown_product_resolution_reason_code', COUNT(*)
  FROM questions, UNNEST(IFNULL(product_resolution_reason_codes, [])) reason
  WHERE reason NOT IN (
    'invalid_product_subject_indexes','product_subject_not_in_demand',
    'product_subject_not_found','product_identity_unresolved',
    'product_resolver_failed','resolved_identity_incomplete',
    'request_spec_unavailable'
  )
  UNION ALL
  SELECT 'unknown_classification_status', COUNTIF(
    classification_status IS NULL
    OR NOT (
      classification_status IN ('classified','unclassified','producer_invalid')
      OR (
        record_origin IN ('firestore_history', 'legacy_audit_history')
        AND classification_status = 'not_measured'
      )
    )
  ) FROM questions
  UNION ALL
  SELECT 'source_question_without_roster', COUNTIF(
    issue_code = 'source_event_without_roster'
    AND event_family = 'question_received'
  ) FROM _run_event_issues
  UNION ALL
  SELECT 'source_answer_without_roster', COUNTIF(
    issue_code = 'source_event_without_roster'
    AND event_family = 'answer_completed'
  ) FROM _run_event_issues
  UNION ALL
  SELECT 'question_without_terminal', COUNTIF(a.event_id IS NULL)
  FROM questions q LEFT JOIN answers a USING (request_id)
  UNION ALL
  SELECT 'answer_without_question', COUNTIF(q.request_id IS NULL)
  FROM answers a LEFT JOIN question_links q USING (request_id)
  UNION ALL
  SELECT 'terminal_without_persistence_measurement', COUNTIF(terminal IS NOT NULL AND message_persisted IS NULL) FROM answers
), classified AS (
  SELECT
    check_name,
    failure_count,
    CASE
      WHEN check_name IN (
        'repaired_duplicate_question_event_id',
        'repaired_duplicate_answer_event_id'
      ) THEN 'repaired'
      WHEN check_name IN (
        'run_manifest_accounting_mismatch',
        'duplicate_question_event_id',
        'duplicate_answer_event_id',
        'duplicate_answer_request_id'
      ) THEN 'batch_blocking'
      WHEN check_name IN (
        'duplicate_source_question_event_id',
        'duplicate_source_answer_event_id',
        'source_event_missing_identity',
        'source_event_missing_correlation',
        'source_event_invalid_timestamp',
        'source_event_ambiguous_roster',
        'unsupported_event_family',
        'source_question_without_roster',
        'source_answer_without_roster',
        'answer_without_question'
      ) THEN 'row_quarantined'
      WHEN check_name IN (
        'unknown_question_category',
        'unknown_secondary_question_category',
        'unknown_analytics_task',
        'missing_analytics_axes',
        'invalid_classification_producer',
        'invalid_classification_semantics',
        'invalid_task_semantics',
        'invalid_product_resolution_counts',
        'invalid_product_resolution_semantics',
        'invalid_product_identity_alignment',
        'unknown_analytics_contract_version',
        'unknown_classification_reason_code',
        'unknown_product_resolution_status',
        'unknown_product_resolution_reason_code',
        'unknown_classification_status'
      ) THEN 'axis_unmeasured'
      ELSE 'coverage'
    END AS disposition
  FROM checks
)
SELECT
  check_name,
  failure_count,
  disposition,
  CASE disposition
    WHEN 'batch_blocking' THEN 'critical'
    WHEN 'row_quarantined' THEN 'warning'
    WHEN 'axis_unmeasured' THEN 'producer_error'
    WHEN 'repaired' THEN 'info'
    ELSE 'coverage'
  END AS severity,
  disposition = 'repaired' OR failure_count = 0 AS passed
FROM classified
ORDER BY check_name;
