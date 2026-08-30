-- Required parameters: @window_start TIMESTAMP, @window_end TIMESTAMP.
-- Every check has an explicit disposition. Only batch_blocking can roll back a
-- publish; row_quarantined and axis_unmeasured remain visible without making
-- an unrelated valid event disappear. Row-level source dispositions are also
-- persisted by merge_incremental.sql in the privacy-safe hashed issue ledger.
WITH valid_trace_contract_registrations AS (
  -- Only the controlled candidate-sample registration can authorize a strict
  -- v2 revision. The recurring publisher is deliberately not a second version
  -- authority and only reads this small durable ledger. Invalid/manual-looking
  -- rows cannot start enforcement: the exact tuple proof must be recomputable.
  SELECT
    revision_name,
    sample_source_ts,
    enforcement_start,
    activation_source,
    promotion_receipt_type,
    promotion_receipt_sha256,
    promotion_project,
    promotion_region,
    promotion_service,
    promotion_target_revision,
    promotion_traffic_readback_at,
    promotion_max_request_timeout_seconds,
    promotion_drain_until,
    promotion_old_positive_revisions_json,
    activation_service_readback_sha256
  FROM `${PROJECT_ID}.${DATASET_ID}.monitor_contract_revision_ledger`
  WHERE monitor_contract_version = 'monitor.v2'
    AND registration_source = 'candidate_v2_exact_http_question_sample'
    AND sample_endpoint_class IN ('ask', 'ask_stream')
    AND NULLIF(revision_name, '') IS NOT NULL
    AND REGEXP_CONTAINS(
      sample_cloud_trace,
      r'^projects/${PROJECT_ID}/traces/[0-9a-f]{32}$'
    )
    AND REGEXP_CONTAINS(sample_cloud_span_id, r'^[0-9a-f]{16}$')
    AND sample_correlation_hash = TO_HEX(SHA256(CONCAT(
      revision_name, '|', sample_cloud_trace, '|', sample_cloud_span_id, '|',
      sample_endpoint_class
    )))
  QUALIFY COUNT(*) OVER (PARTITION BY revision_name) = 1
), trace_contract_revisions AS (
  SELECT revision_name
  FROM valid_trace_contract_registrations
), trace_contract_enforcement AS (
  -- Candidate registration makes that immutable revision strict immediately,
  -- but unknown revisions remain legacy coverage until a separately verified
  -- post-promotion drain activation is durably recorded on the same row.
  SELECT MIN(enforcement_start) AS enforcement_start
  FROM valid_trace_contract_registrations
  WHERE activation_source = 'lcs_promotion_v2_drained_live_readback'
    AND promotion_receipt_type = 'lcs_candidate_promotion_v2'
    AND REGEXP_CONTAINS(promotion_receipt_sha256, r'^[0-9a-f]{64}$')
    AND promotion_project = '${PROJECT_ID}'
    AND NULLIF(promotion_region, '') IS NOT NULL
    AND promotion_service = '${SOURCE_SERVICE}'
    AND promotion_target_revision = revision_name
    AND promotion_traffic_readback_at IS NOT NULL
    AND promotion_max_request_timeout_seconds BETWEEN 1 AND 3600
    AND promotion_drain_until = TIMESTAMP_ADD(
      promotion_traffic_readback_at,
      INTERVAL promotion_max_request_timeout_seconds SECOND
    )
    AND enforcement_start >= promotion_drain_until
    AND enforcement_start <= CURRENT_TIMESTAMP()
    AND SAFE.PARSE_JSON(promotion_old_positive_revisions_json) IS NOT NULL
    AND JSON_TYPE(SAFE.PARSE_JSON(promotion_old_positive_revisions_json)) = 'array'
    AND NOT EXISTS (
      SELECT 1
      FROM UNNEST(IFNULL(
        JSON_QUERY_ARRAY(SAFE.PARSE_JSON(promotion_old_positive_revisions_json)),
        []
      )) old_revision
      WHERE NULLIF(JSON_VALUE(old_revision, '$.revisionName'), '') IS NULL
        OR JSON_VALUE(old_revision, '$.revisionName') = revision_name
        OR SAFE_CAST(JSON_VALUE(old_revision, '$.percent') AS INT64) IS NULL
        OR SAFE_CAST(JSON_VALUE(old_revision, '$.percent') AS INT64) NOT BETWEEN 1 AND 100
        OR SAFE_CAST(JSON_VALUE(old_revision, '$.timeoutSeconds') AS INT64) IS NULL
        OR SAFE_CAST(JSON_VALUE(old_revision, '$.timeoutSeconds') AS INT64) NOT BETWEEN 1 AND 3600
    )
    AND ARRAY_LENGTH(IFNULL(
      JSON_QUERY_ARRAY(SAFE.PARSE_JSON(promotion_old_positive_revisions_json)),
      []
    )) > 0
    AND (
      SELECT SUM(SAFE_CAST(JSON_VALUE(old_revision, '$.percent') AS INT64))
      FROM UNNEST(IFNULL(
        JSON_QUERY_ARRAY(SAFE.PARSE_JSON(promotion_old_positive_revisions_json)),
        []
      )) old_revision
    ) = 100
    AND (
      SELECT COUNT(DISTINCT JSON_VALUE(old_revision, '$.revisionName'))
      FROM UNNEST(IFNULL(
        JSON_QUERY_ARRAY(SAFE.PARSE_JSON(promotion_old_positive_revisions_json)),
        []
      )) old_revision
    ) = ARRAY_LENGTH(IFNULL(
      JSON_QUERY_ARRAY(SAFE.PARSE_JSON(promotion_old_positive_revisions_json)),
      []
    ))
    AND promotion_max_request_timeout_seconds = COALESCE((
      SELECT MAX(SAFE_CAST(JSON_VALUE(old_revision, '$.timeoutSeconds') AS INT64))
      FROM UNNEST(IFNULL(
        JSON_QUERY_ARRAY(SAFE.PARSE_JSON(promotion_old_positive_revisions_json)),
        []
      )) old_revision
    ), 0)
    AND REGEXP_CONTAINS(activation_service_readback_sha256, r'^[0-9a-f]{64}$')
), source_question_correlation_rows AS (
  SELECT
    COALESCE(NULLIF(event_id, ''), source_event_hash) AS event_key,
    revision_name,
    cloud_trace,
    cloud_span_id,
    endpoint_class
  FROM _run_monitor_source source
  JOIN trace_contract_revisions contract USING (revision_name)
  WHERE event_family = 'question_received'
    AND monitor_contract_version = 'monitor.v2'
    AND endpoint_class IN (
      'ask', 'ask_stream', 'debug_ask', 'debug_ask_stream'
    )
    AND delivery_row_number = 1
    AND NULLIF(event_id, '') IS NOT NULL
    AND NULLIF(revision_name, '') IS NOT NULL
    AND NULLIF(cloud_trace, '') IS NOT NULL
    AND NULLIF(cloud_span_id, '') IS NOT NULL
), source_question_correlations AS (
  SELECT
    revision_name,
    cloud_trace,
    cloud_span_id,
    endpoint_class,
    COUNT(DISTINCT event_key) AS question_count
  FROM source_question_correlation_rows
  GROUP BY revision_name, cloud_trace, cloud_span_id, endpoint_class
), completed_ask_request_rows AS (
  SELECT
    COALESCE(
      NULLIF(insert_id, ''),
      TO_HEX(SHA256(TO_JSON_STRING(STRUCT(source_ts, method, request_url, status))))
    ) AS request_key,
    cloud_trace,
    cloud_span_id,
    revision_name,
    endpoint_class,
    status,
    source_ts
  FROM `${PROJECT_ID}.${DATASET_ID}.http_request_source`
  WHERE source_ts >= @window_start AND source_ts < @window_end
    AND method = 'POST'
    AND status IS NOT NULL
    AND endpoint_class IN (
      'ask', 'ask_stream', 'debug_ask', 'debug_ask_stream'
    )
), accepted_ask_request_rows AS (
  SELECT *
  FROM completed_ask_request_rows
  WHERE status BETWEEN 200 AND 299
), trace_enforced_completed_ask_request_rows AS (
  SELECT request.*
  FROM completed_ask_request_rows request
  JOIN trace_contract_revisions contract USING (revision_name)
), trace_enforced_completed_ask_request_counts AS (
  SELECT
    request.revision_name,
    request.cloud_trace,
    request.cloud_span_id,
    request.endpoint_class,
    COUNT(DISTINCT request.request_key) AS request_count
  FROM trace_enforced_completed_ask_request_rows request
  GROUP BY revision_name, cloud_trace, cloud_span_id, endpoint_class
), trace_enforced_ask_request_rows AS (
  SELECT request.*
  FROM accepted_ask_request_rows request
  JOIN trace_contract_revisions contract USING (revision_name)
), trace_enforced_ask_request_counts AS (
  SELECT
    request.revision_name,
    request.cloud_trace,
    request.cloud_span_id,
    request.endpoint_class,
    COUNT(DISTINCT request.request_key) AS request_count
  FROM trace_enforced_ask_request_rows request
  GROUP BY revision_name, cloud_trace, cloud_span_id, endpoint_class
), http_event_route_mismatches AS (
  SELECT DISTINCT request.request_key
  FROM trace_enforced_completed_ask_request_rows request
  JOIN source_question_correlation_rows question
    USING (revision_name, cloud_trace, cloud_span_id)
  WHERE request.endpoint_class != question.endpoint_class
), monitor_v2_question_http_cardinality AS (
  SELECT
    question.revision_name,
    question.cloud_trace,
    question.cloud_span_id,
    question.endpoint_class,
    question.question_count,
    COALESCE(request.request_count, 0) AS request_count
  FROM source_question_correlations question
  LEFT JOIN trace_enforced_completed_ask_request_counts request
    USING (revision_name, cloud_trace, cloud_span_id, endpoint_class)
), business_request_ids AS (
  SELECT DISTINCT request_id
  FROM _run_admissible_monitor_events
  WHERE event_family IN ('question_received', 'answer_completed', 'message_persisted')
    AND endpoint_class IN ('ask', 'ask_stream')
    AND NULLIF(request_id, '') IS NOT NULL
), questions AS (
  SELECT question.*
  FROM `${PROJECT_ID}.${DATASET_ID}.question_events` question
  JOIN business_request_ids affected USING (request_id)
  WHERE question.question_date BETWEEN
    DATE_SUB(event_partition_start, INTERVAL 1 DAY)
    AND DATE_ADD(event_partition_end, INTERVAL 1 DAY)
), answers AS (
  SELECT answer.*
  FROM `${PROJECT_ID}.${DATASET_ID}.answer_events` answer
  JOIN business_request_ids affected USING (request_id)
  WHERE answer.answer_date BETWEEN
    DATE_SUB(affected_answer_partition_start, INTERVAL 1 DAY)
    AND DATE_ADD(affected_answer_partition_end, INTERVAL 1 DAY)
), question_links AS (
  SELECT DISTINCT question.request_id
  FROM `${PROJECT_ID}.${DATASET_ID}.question_events` question
  JOIN business_request_ids affected USING (request_id)
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
  SELECT 'accepted_http_without_question_event', COALESCE(SUM(
    CASE
      WHEN request.request_count = 1 AND question.question_count = 1 THEN 0
      ELSE GREATEST(request.request_count, COALESCE(question.question_count, 0))
    END
  ), 0)
  FROM trace_enforced_ask_request_counts request
  LEFT JOIN source_question_correlations question
    USING (revision_name, cloud_trace, cloud_span_id, endpoint_class)
  UNION ALL
  SELECT 'http_event_route_class_mismatch', COUNT(DISTINCT request_key)
  FROM http_event_route_mismatches
  UNION ALL
  SELECT 'monitor_v2_question_completed_http_cardinality_mismatch', COALESCE(SUM(
    CASE
      WHEN question_count = 1 AND request_count = 1 THEN 0
      ELSE GREATEST(question_count, request_count)
    END
  ), 0)
  FROM monitor_v2_question_http_cardinality
  UNION ALL
  SELECT 'monitor_v2_event_missing_http_correlation_fields', COUNTIF(
    event.monitor_contract_version = 'monitor.v2'
    AND event.delivery_row_number = 1
    AND event.event_family IN (
      'question_received', 'answer_completed', 'message_persisted', 'answer_action'
    )
    AND (
      NULLIF(event.cloud_trace, '') IS NULL
      OR NULLIF(event.cloud_span_id, '') IS NULL
    )
  )
  FROM _run_monitor_source event
  JOIN trace_contract_revisions contract USING (revision_name)
  UNION ALL
  SELECT 'unexpected_monitor_v2_revision_after_enforcement', COUNT(*)
  FROM _run_monitor_source event
  CROSS JOIN trace_contract_enforcement enforcement
  WHERE event.monitor_contract_version = 'monitor.v2'
    AND event.delivery_row_number = 1
    AND enforcement.enforcement_start IS NOT NULL
    AND event.source_ts >= enforcement.enforcement_start
    AND NOT EXISTS (
      SELECT 1
      FROM trace_contract_revisions contract
      WHERE contract.revision_name = event.revision_name
    )
  UNION ALL
  SELECT 'unexpected_accepted_business_http_revision_after_enforcement',
    COUNT(DISTINCT request.request_key)
  FROM accepted_ask_request_rows request
  CROSS JOIN trace_contract_enforcement enforcement
  WHERE enforcement.enforcement_start IS NOT NULL
    AND request.source_ts >= enforcement.enforcement_start
    AND request.endpoint_class IN ('ask', 'ask_stream')
    AND NOT EXISTS (
      SELECT 1
      FROM trace_contract_revisions contract
      WHERE contract.revision_name = request.revision_name
    )
  UNION ALL
  SELECT 'legacy_unregistered_monitor_v2_revision', COUNT(*)
  FROM _run_monitor_source event
  CROSS JOIN trace_contract_enforcement enforcement
  WHERE event.monitor_contract_version = 'monitor.v2'
    AND event.delivery_row_number = 1
    AND (
      enforcement.enforcement_start IS NULL
      OR event.source_ts < enforcement.enforcement_start
    )
    AND NOT EXISTS (
      SELECT 1
      FROM trace_contract_revisions contract
      WHERE contract.revision_name = event.revision_name
    )
  UNION ALL
  SELECT 'monitor_v2_question_invalid_endpoint_class', COUNTIF(
    event.monitor_contract_version = 'monitor.v2'
    AND event.delivery_row_number = 1
    AND event.event_family = 'question_received'
    AND IFNULL(event.endpoint_class, '') NOT IN (
      'ask', 'ask_stream', 'debug_ask', 'debug_ask_stream'
    )
  )
  FROM _run_monitor_source event
  JOIN trace_contract_revisions contract USING (revision_name)
  UNION ALL
  SELECT 'monitor_v2_http_missing_trace_context', COUNT(DISTINCT request_key)
  FROM trace_enforced_completed_ask_request_rows
  WHERE NULLIF(cloud_trace, '') IS NULL
    OR NULLIF(cloud_span_id, '') IS NULL
  UNION ALL
  SELECT 'monitor_v2_revision_contract_downgrade', COUNT(*)
  FROM _run_monitor_source event
  JOIN trace_contract_revisions contract USING (revision_name)
  WHERE event.delivery_row_number = 1
    AND IFNULL(event.monitor_contract_version, '') != 'monitor.v2'
  UNION ALL
  SELECT 'http_trace_contract_unavailable', COUNT(DISTINCT request_key)
  FROM accepted_ask_request_rows request
  WHERE NULLIF(request.revision_name, '') IS NULL
    OR NOT EXISTS (
      SELECT 1
      FROM trace_contract_revisions contract
      WHERE contract.revision_name = request.revision_name
    )
  UNION ALL
  SELECT 'invalid_current_question_event_contract', COUNTIF(
    event_family = 'question_received'
    AND endpoint_class IN ('ask', 'ask_stream')
    AND delivery_row_number = 1
    AND SAFE_CAST(JSON_VALUE(payload_json, '$.valid_question') AS BOOL) IS NOT TRUE
  ) FROM _run_monitor_source
  UNION ALL
  SELECT 'unknown_question_category', COUNTIF(
    IFNULL(record_origin, '') NOT IN ('firestore_history', 'legacy_audit_history')
    AND (
      primary_question_category IS NULL
      OR primary_question_category NOT IN (
        'product_information','price_product_code','comparison_fit_selection','usage_procedure',
        'troubleshooting_safety','sales_proposal','institution_gpo_market','document_search',
        'other_general','unclassified'
      )
    )
  ) FROM questions
  UNION ALL
  SELECT 'unknown_secondary_question_category', COUNT(*)
  FROM questions q, UNNEST(IFNULL(q.question_categories, [])) category
  WHERE IFNULL(q.record_origin, '') NOT IN ('firestore_history', 'legacy_audit_history')
    AND category NOT IN (
    'product_information','price_product_code','comparison_fit_selection','usage_procedure',
    'troubleshooting_safety','sales_proposal','institution_gpo_market','document_search',
    'other_general','unclassified'
  )
  UNION ALL
  SELECT 'unknown_analytics_task', COUNT(*)
  FROM questions q, UNNEST(IFNULL(q.analytics_tasks, [])) task
  WHERE IFNULL(q.record_origin, '') NOT IN ('firestore_history', 'legacy_audit_history')
    AND task NOT IN (
    'fact_lookup','explanation','comparison_selection','procedure_guidance',
    'troubleshooting','content_creation','source_retrieval','market_research',
    'other','unclassified'
  )
  UNION ALL
  SELECT 'missing_analytics_axes', COUNTIF(
    IFNULL(record_origin, '') NOT IN ('firestore_history', 'legacy_audit_history')
    AND (
      IFNULL(ARRAY_LENGTH(question_categories), 0) = 0
      OR IFNULL(ARRAY_LENGTH(analytics_tasks), 0) = 0
    )
  ) FROM questions
  UNION ALL
  SELECT 'invalid_classification_producer', COUNTIF(
    IFNULL(record_origin, '') NOT IN ('firestore_history', 'legacy_audit_history')
    AND classification_status = 'producer_invalid'
  ) FROM questions
  UNION ALL
  SELECT 'invalid_task_producer', COUNTIF(
    IFNULL(record_origin, '') NOT IN ('firestore_history', 'legacy_audit_history')
    AND analytics_contract_version = 'request_spec_analytics_v2'
    AND EXISTS (
      SELECT 1
      FROM UNNEST(IFNULL(classification_reason_codes, [])) reason
      WHERE reason IN ('invalid_analytics_task', 'request_spec_unavailable')
    )
  ) FROM questions
  UNION ALL
  SELECT 'invalid_product_producer', COUNTIF(
    IFNULL(record_origin, '') NOT IN ('firestore_history', 'legacy_audit_history')
    AND analytics_contract_version = 'request_spec_analytics_v2'
    AND product_resolution_status IN ('producer_invalid', 'resolver_failed')
  ) FROM questions
  UNION ALL
  SELECT 'invalid_classification_semantics', COUNT(*)
  FROM questions q
  WHERE IFNULL(q.record_origin, '') NOT IN ('firestore_history', 'legacy_audit_history')
    AND q.analytics_contract_version = 'request_spec_analytics_v2'
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
  WHERE IFNULL(q.record_origin, '') NOT IN ('firestore_history', 'legacy_audit_history')
    AND q.analytics_contract_version = 'request_spec_analytics_v2'
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
    IFNULL(record_origin, '') NOT IN ('firestore_history', 'legacy_audit_history')
    AND (
      product_candidate_count IS NULL OR product_resolved_count IS NULL
      OR product_candidate_count < 0 OR product_resolved_count < 0
      OR product_resolved_count > product_candidate_count
    )
  ) FROM questions
  UNION ALL
  SELECT 'invalid_product_resolution_semantics', COUNT(*)
  FROM questions q
  WHERE IFNULL(q.record_origin, '') NOT IN ('firestore_history', 'legacy_audit_history')
    AND q.analytics_contract_version = 'request_spec_analytics_v2'
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
  WHERE IFNULL(q.record_origin, '') NOT IN ('firestore_history', 'legacy_audit_history')
    AND q.analytics_contract_version = 'request_spec_analytics_v2'
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
    IFNULL(record_origin, '') NOT IN ('firestore_history', 'legacy_audit_history')
    AND analytics_contract_version IS NOT NULL
    AND analytics_contract_version NOT IN ('request_spec_analytics_v1', 'request_spec_analytics_v2')
  ) FROM questions
  UNION ALL
  SELECT 'missing_current_analytics_contract_version', COUNTIF(
    IFNULL(record_origin, '') NOT IN ('firestore_history', 'legacy_audit_history')
    AND NULLIF(analytics_contract_version, '') IS NULL
  ) FROM questions
  UNION ALL
  SELECT 'unknown_classification_reason_code', COUNT(*)
  FROM questions q, UNNEST(IFNULL(q.classification_reason_codes, [])) reason
  WHERE IFNULL(q.record_origin, '') NOT IN ('firestore_history', 'legacy_audit_history')
    AND reason NOT IN (
    'invalid_question_category','invalid_analytics_task','request_spec_unavailable'
  )
  UNION ALL
  SELECT 'unknown_product_resolution_status', COUNTIF(
    IFNULL(record_origin, '') NOT IN ('firestore_history', 'legacy_audit_history')
    AND analytics_contract_version = 'request_spec_analytics_v2'
    AND IFNULL(product_resolution_status, '') NOT IN (
      'not_applicable','resolved','partially_resolved','unresolved',
      'producer_invalid','resolver_failed'
    )
  ) FROM questions
  UNION ALL
  SELECT 'unknown_product_resolution_reason_code', COUNT(*)
  FROM questions q, UNNEST(IFNULL(q.product_resolution_reason_codes, [])) reason
  WHERE IFNULL(q.record_origin, '') NOT IN ('firestore_history', 'legacy_audit_history')
    AND reason NOT IN (
    'invalid_product_subject_indexes','product_subject_not_in_demand',
    'product_subject_not_found','product_identity_unresolved',
    'product_resolver_failed','resolved_identity_incomplete',
    'request_spec_unavailable'
  )
  UNION ALL
  SELECT 'unknown_classification_status', COUNTIF(
    IFNULL(record_origin, '') NOT IN ('firestore_history', 'legacy_audit_history')
    AND (
      classification_status IS NULL
      OR classification_status NOT IN ('classified','unclassified','producer_invalid')
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
  SELECT 'question_without_terminal', COUNTIF(
    IFNULL(q.record_origin, '') NOT IN ('firestore_history', 'legacy_audit_history')
    AND a.event_id IS NULL
  )
  FROM questions q LEFT JOIN answers a USING (request_id)
  UNION ALL
  SELECT 'answer_without_question', COUNTIF(
    IFNULL(a.record_origin, '') NOT IN ('firestore_history', 'legacy_audit_history')
    AND q.request_id IS NULL
  )
  FROM answers a LEFT JOIN question_links q USING (request_id)
  UNION ALL
  SELECT 'invalid_current_terminal_contract', COUNTIF(
    IFNULL(a.record_origin, '') NOT IN ('firestore_history', 'legacy_audit_history')
    AND a.analytics_contract_version = 'request_spec_analytics_v2'
    AND (
      IFNULL(a.terminal, '') NOT IN ('final', 'error')
      OR IFNULL(a.runtime_status, '') NOT IN ('completed', 'failed')
      OR (a.terminal = 'final') != (a.runtime_status = 'completed')
    )
  ) FROM answers a
  UNION ALL
  SELECT 'current_final_without_persistence_measurement', COUNTIF(
    IFNULL(a.record_origin, '') NOT IN ('firestore_history', 'legacy_audit_history')
    AND a.terminal = 'final'
    AND a.runtime_status = 'completed'
    AND (
      a.message_persisted IS NULL
      OR a.assistant_error_present IS NULL
    )
  ) FROM answers a
  UNION ALL
  SELECT 'current_final_without_demand_measurement', COUNTIF(
    IFNULL(a.record_origin, '') NOT IN ('firestore_history', 'legacy_audit_history')
    AND a.terminal = 'final'
    AND a.runtime_status = 'completed'
    AND (
      a.demand_total IS NULL OR a.demand_total <= 0
      OR a.partial_demand_count IS NULL OR a.omitted_demand_count IS NULL
      OR a.system_fault_count IS NULL
    )
  ) FROM answers a
  UNION ALL
  SELECT 'current_terminal_without_latency_measurement', COUNTIF(
    IFNULL(a.record_origin, '') NOT IN ('firestore_history', 'legacy_audit_history')
    AND a.terminal IS NOT NULL
    AND (a.total_latency_ms IS NULL OR a.total_latency_ms < 0)
  ) FROM answers a
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
        'duplicate_answer_request_id',
        'accepted_http_without_question_event',
        'http_event_route_class_mismatch',
        'monitor_v2_question_completed_http_cardinality_mismatch',
        'monitor_v2_event_missing_http_correlation_fields',
        'unexpected_monitor_v2_revision_after_enforcement',
        'unexpected_accepted_business_http_revision_after_enforcement',
        'monitor_v2_question_invalid_endpoint_class',
        'monitor_v2_http_missing_trace_context',
        'monitor_v2_revision_contract_downgrade',
        'invalid_current_question_event_contract',
        'question_without_terminal',
        'answer_without_question',
        'invalid_current_terminal_contract',
        'current_final_without_persistence_measurement',
        'current_final_without_demand_measurement',
        'current_terminal_without_latency_measurement',
        'unknown_question_category',
        'unknown_secondary_question_category',
        'unknown_analytics_task',
        'missing_analytics_axes',
        'invalid_classification_semantics',
        'invalid_task_semantics',
        'invalid_product_resolution_counts',
        'invalid_product_resolution_semantics',
        'invalid_product_identity_alignment',
        'missing_current_analytics_contract_version',
        'unknown_analytics_contract_version',
        'unknown_classification_reason_code',
        'unknown_product_resolution_status',
        'unknown_product_resolution_reason_code',
        'unknown_classification_status'
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
        'source_answer_without_roster'
      ) THEN 'row_quarantined'
      -- Current v2 producers own every analytics axis. Producer-invalid and
      -- resolver-failed rows may remain visible in a failed-run diagnostic,
      -- but they must never advance the published snapshot as a partial
      -- current measurement. Historical rows with genuinely absent legacy
      -- fields remain non-blocking through their separate history contract.
      WHEN check_name IN (
        'invalid_classification_producer',
        'invalid_task_producer',
        'invalid_product_producer'
      ) THEN 'batch_blocking'
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
