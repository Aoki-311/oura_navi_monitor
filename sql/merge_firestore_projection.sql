-- Required parameters: @user_scope_rows, @conversation_rows, @citation_rows,
-- @conversation_partition_start, @conversation_partition_end,
-- @citation_partition_start, @citation_partition_end.

DELETE FROM `${PROJECT_ID}.${DATASET_ID}.user_scope` WHERE TRUE;

INSERT INTO `${PROJECT_ID}.${DATASET_ID}.user_scope` (
  roster_id, user_id, area, area_key, workplace, role, department,
  mr_experience, is_active, global_scope_enabled, user_map_scope_enabled,
  is_admin, updated_at
)
SELECT
  roster_id, user_id, area, area_key, workplace, role, department,
  mr_experience, is_active, global_scope_enabled, user_map_scope_enabled,
  is_admin, updated_at
FROM UNNEST(@user_scope_rows);

MERGE `${PROJECT_ID}.${DATASET_ID}.conversation_events` target
USING (SELECT * FROM UNNEST(@conversation_rows)) source
ON target.event_id = source.event_id
AND target.updated_date BETWEEN @conversation_partition_start AND @conversation_partition_end
WHEN MATCHED THEN UPDATE SET
  conversation_id = source.conversation_id,
  user_id = source.user_id,
  roster_id = source.roster_id,
  first_active_at = source.first_active_at,
  last_active_at = source.last_active_at,
  updated_date = source.updated_date,
  user_message_count = source.user_message_count,
  assistant_message_count = source.assistant_message_count,
  followup_count = source.followup_count,
  active_days = source.active_days,
  primary_mode = source.primary_mode,
  status = source.status,
  source_event_ts = source.source_event_ts,
  materialized_at = source.materialized_at
WHEN NOT MATCHED THEN INSERT ROW;

MERGE `${PROJECT_ID}.${DATASET_ID}.citation_events` target
USING (SELECT * FROM UNNEST(@citation_rows)) source
ON target.event_id = source.event_id
AND target.answer_date BETWEEN @citation_partition_start AND @citation_partition_end
WHEN MATCHED THEN UPDATE SET
  answer_event_id = source.answer_event_id,
  answer_ts = source.answer_ts,
  answer_date = source.answer_date,
  user_id = source.user_id,
  roster_id = source.roster_id,
  message_id = source.message_id,
  citation_order = source.citation_order,
  source_type = source.source_type,
  source_system = source.source_system,
  document_key = source.document_key,
  display_title = source.display_title,
  page_number = source.page_number,
  access_status = source.access_status,
  trust_tier = source.trust_tier,
  primary_product_key = source.primary_product_key,
  source_event_ts = source.source_event_ts,
  materialized_at = source.materialized_at
WHEN NOT MATCHED THEN INSERT ROW;
