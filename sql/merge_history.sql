-- Required parameters: @history_partition_date plus @history_questions,
-- @history_answers, @history_conversations, and @history_citations. The scalar
-- date is an explicit target-table partition bound; relying only on a source
-- field equality does not satisfy BigQuery require_partition_filter.
-- This one-time compiler writes the same canonical facts as the incremental
-- owner; it creates no second table.

MERGE `${PROJECT_ID}.${DATASET_ID}.question_events` target
USING UNNEST(@history_questions) source
ON target.event_id = source.event_id
  AND target.question_date = source.question_date
  AND target.question_date = @history_partition_date
WHEN MATCHED AND target.record_origin IN ('firestore_history', 'legacy_audit_history') THEN UPDATE SET
  question_ts = source.question_ts,
  user_id = source.user_id,
  roster_id = source.roster_id,
  request_id = source.request_id,
  trace_id = NULLIF(source.trace_id, ''),
  conversation_id = source.conversation_id,
  turn_id = NULLIF(source.turn_id, ''),
  message_id = source.message_id,
  mode = NULLIF(source.mode, ''),
  device_class = NULLIF(source.device_class, ''),
  attachment_count = source.attachment_count,
  producer_revision = NULLIF(source.producer_revision, ''),
  producer_git_sha = NULLIF(source.producer_git_sha, ''),
  primary_question_category = source.question_category,
  question_categories = [source.question_category],
  classification_status = source.classification_status,
  analytics_tasks = ARRAY<STRING>[],
  record_origin = source.record_origin,
  measurement_profile = source.measurement_profile,
  source_event_ts = source.question_ts,
  materialized_at = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (
  event_id, question_ts, question_date, user_id, roster_id, request_id, trace_id,
  conversation_id, turn_id, message_id, mode, device_class, endpoint_class,
  valid_question, attachment_count, primary_question_category,
  question_categories, classification_status, is_multi_intent, analytics_tasks,
  primary_product_key, primary_product_name, product_keys, product_names,
  product_candidate_count, product_resolved_count, producer_revision,
  producer_git_sha, record_origin, measurement_profile, source_event_ts,
  materialized_at
) VALUES (
  source.event_id, source.question_ts, source.question_date, source.user_id,
  source.roster_id, source.request_id, NULLIF(source.trace_id, ''),
  source.conversation_id, NULLIF(source.turn_id, ''), source.message_id,
  NULLIF(source.mode, ''), NULLIF(source.device_class, ''), 'ask_stream', TRUE,
  source.attachment_count, source.question_category, [source.question_category],
  source.classification_status,
  FALSE, ARRAY<STRING>[], NULL, NULL, [], [], 0, 0,
  NULLIF(source.producer_revision, ''), NULLIF(source.producer_git_sha, ''),
  source.record_origin, source.measurement_profile, source.question_ts,
  CURRENT_TIMESTAMP()
);

MERGE `${PROJECT_ID}.${DATASET_ID}.answer_events` target
USING UNNEST(@history_answers) source
ON target.event_id = source.event_id
  AND target.answer_date = source.answer_date
  AND target.answer_date = @history_partition_date
WHEN MATCHED AND target.record_origin IN ('firestore_history', 'legacy_audit_history') THEN UPDATE SET
  answer_ts = source.answer_ts,
  user_id = source.user_id,
  roster_id = source.roster_id,
  request_id = source.request_id,
  trace_id = NULLIF(source.trace_id, ''),
  conversation_id = source.conversation_id,
  turn_id = NULLIF(source.turn_id, ''),
  message_id = source.message_id,
  mode = NULLIF(source.mode, ''),
  device_class = NULLIF(source.device_class, ''),
  terminal = NULLIF(source.terminal, ''),
  runtime_status = NULLIF(source.runtime_status, ''),
  failure_code = NULLIF(source.failure_code, ''),
  analytics_tasks = ARRAY<STRING>[],
  demand_total = source.demand_total,
  delivered_demand_count = source.delivered_demand_count,
  partial_demand_count = source.partial_demand_count,
  omitted_demand_count = source.omitted_demand_count,
  system_fault_count = source.system_fault_count,
  total_latency_ms = source.total_latency_ms,
  stage_latency_ms = SAFE.PARSE_JSON(NULLIF(source.stage_latency_json, '')),
  writer_error_code = NULLIF(source.writer_error_code, ''),
  message_persisted = source.message_persisted,
  assistant_error_present = source.assistant_error_present,
  measurement_available = source.measurement_available,
  complete_delivery = source.complete_delivery,
  primary_failure_reason = NULLIF(source.primary_failure_reason, ''),
  revision_name = NULLIF(source.revision_name, ''),
  git_sha = NULLIF(source.git_sha, ''),
  record_origin = source.record_origin,
  measurement_profile = source.measurement_profile,
  source_event_ts = source.answer_ts,
  materialized_at = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (
  event_id, answer_ts, answer_date, user_id, roster_id, request_id, trace_id,
  conversation_id, turn_id, message_id, mode, device_class, terminal,
  runtime_status, failure_code, primary_question_category, question_categories,
  classification_status, is_multi_intent, analytics_tasks,
  product_candidate_count, product_resolved_count, demand_total,
  delivered_demand_count, partial_demand_count, omitted_demand_count,
  system_fault_count, total_latency_ms, stage_latency_ms, writer_error_code,
  message_persisted, assistant_error_present, measurement_available,
  complete_delivery, primary_failure_reason, revision_name, git_sha,
  record_origin, measurement_profile, source_event_ts, materialized_at
) VALUES (
  source.event_id, source.answer_ts, source.answer_date, source.user_id,
  source.roster_id, source.request_id, NULLIF(source.trace_id, ''),
  source.conversation_id, NULLIF(source.turn_id, ''), source.message_id,
  NULLIF(source.mode, ''), NULLIF(source.device_class, ''),
  NULLIF(source.terminal, ''), NULLIF(source.runtime_status, ''),
  NULLIF(source.failure_code, ''), 'unclassified', ['unclassified'],
  'not_measured', FALSE, ARRAY<STRING>[], 0, 0, source.demand_total,
  source.delivered_demand_count, source.partial_demand_count,
  source.omitted_demand_count, source.system_fault_count,
  source.total_latency_ms, SAFE.PARSE_JSON(NULLIF(source.stage_latency_json, '')),
  NULLIF(source.writer_error_code, ''), source.message_persisted,
  source.assistant_error_present,
  source.measurement_available, source.complete_delivery,
  NULLIF(source.primary_failure_reason, ''), NULLIF(source.revision_name, ''),
  NULLIF(source.git_sha, ''), source.record_origin, source.measurement_profile,
  source.answer_ts, CURRENT_TIMESTAMP()
);

MERGE `${PROJECT_ID}.${DATASET_ID}.conversation_events` target
USING UNNEST(@history_conversations) source
ON target.event_id = source.event_id
  AND target.updated_date = source.updated_date
  AND target.updated_date = @history_partition_date
WHEN MATCHED THEN UPDATE SET
  conversation_id = source.conversation_id, user_id = source.user_id,
  roster_id = source.roster_id, first_active_at = source.first_active_at,
  last_active_at = source.last_active_at,
  user_message_count = source.user_message_count,
  assistant_message_count = source.assistant_message_count,
  followup_count = source.followup_count, active_days = source.active_days,
  primary_mode = source.primary_mode, status = source.status,
  source_event_ts = source.source_event_ts,
  materialized_at = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (
  event_id, conversation_id, user_id, roster_id, first_active_at,
  last_active_at, updated_date, user_message_count, assistant_message_count,
  followup_count, active_days, primary_mode, status, source_event_ts,
  materialized_at
) VALUES (
  source.event_id, source.conversation_id, source.user_id, source.roster_id,
  source.first_active_at, source.last_active_at, source.updated_date,
  source.user_message_count, source.assistant_message_count,
  source.followup_count, source.active_days, source.primary_mode, source.status,
  source.source_event_ts, source.materialized_at
);

MERGE `${PROJECT_ID}.${DATASET_ID}.citation_events` target
USING UNNEST(@history_citations) source
ON target.event_id = source.event_id
  AND target.answer_date = source.answer_date
  AND target.answer_date = @history_partition_date
WHEN MATCHED THEN UPDATE SET
  answer_event_id = source.answer_event_id, answer_ts = source.answer_ts,
  user_id = source.user_id, roster_id = source.roster_id,
  message_id = source.message_id, citation_order = source.citation_order,
  source_type = source.source_type, source_system = source.source_system,
  document_key = source.document_key, display_title = source.display_title,
  page_number = source.page_number, access_status = source.access_status,
  trust_tier = source.trust_tier,
  primary_product_key = source.primary_product_key,
  source_event_ts = source.source_event_ts,
  materialized_at = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (
  event_id, answer_event_id, answer_ts, answer_date, user_id, roster_id,
  message_id, citation_order, source_type, source_system, document_key,
  display_title, page_number, access_status, trust_tier,
  primary_product_key, source_event_ts, materialized_at
) VALUES (
  source.event_id, source.answer_event_id, source.answer_ts,
  source.answer_date, source.user_id, source.roster_id, source.message_id,
  source.citation_order, source.source_type, source.source_system,
  source.document_key, source.display_title, source.page_number,
  source.access_status, source.trust_tier, source.primary_product_key,
  source.source_event_ts, source.materialized_at
);
