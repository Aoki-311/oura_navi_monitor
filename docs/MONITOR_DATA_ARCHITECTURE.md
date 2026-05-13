# 監視データ基盤アーキテクチャ

## 1. 目的

本ドキュメントは、OurA Navi Monitor のデータ保存、加工、join、権限、フロントエンド連携の基盤方針を固定するための設計文書です。

フロントエンドは管理者向けの分かりやすい画面を提供しますが、裏側では回答、追問、会話、メッセージ、根拠、エラー、schema 健全性を一貫したキーで追跡できる必要があります。

## 2. 基本方針

| 方針 | 内容 |
| --- | --- |
| Raw を残す | 原始 payload は必ず保持し、projection や集計の再作成に使えるようにする。 |
| 表示用に整える | フロントエンドは raw payload を直接読まず、projection / aggregate API を参照する。 |
| join key を固定する | `conversation_id + turn_id`、`conversation_id + message_id`、`trace_id + request_id` を主 join とする。 |
| 個人情報を分離する | `user_id_hash` を分析主キーにし、`user_email` と本文は権限制御する。 |
| UI と export を同じ口径にする | 画面表示とエクスポートが別計算にならないよう、同じ projection / aggregate を使う。 |

## 3. データソース

| ソース | 内容 | 主な用途 |
| --- | --- | --- |
| Cloud Run request logs | HTTP status、latency、path、user agent | リクエスト量、エラー率、P95、device 分布 |
| Cloud Run stdout/stderr logs | monitor event payload | ask audit、follow-up、coverage gap、query suggest、sync telemetry |
| Firestore `chat_users` | user、conversation、message、runtime state | ユーザー一覧、会話一覧、メッセージ確認、feedback |
| BigQuery views / projected tables | 正規化済み分析データ | フロントエンド API、エクスポート、日次集計 |

## 4. レイヤー構成

| レイヤー | 目的 | 主なテーブル / View |
| --- | --- | --- |
| Raw layer | 原始イベントを欠落なく保存する | `raw_monitor_events` |
| Projection layer | UI / 分析に使いやすい形へ正規化する | `monitor_answer_events`, `monitor_followup_events`, `monitor_message_state` |
| Aggregate layer | ダッシュボードが直接参照する集計を作る | `monitor_system_daily`, `monitor_user_daily`, `monitor_schema_quality_daily` |
| Export layer | 管理者が選択条件で出力する | export query / export job output |

MVP では物理テーブルではなく BigQuery view で開始してもよいです。ただし API 側の返却契約は、将来 table 化しても変えない前提で固定します。

## 5. Raw Layer

### 5.1 `raw_monitor_events`

すべての monitor event は、まず統一 raw 形式として扱います。

| Field | 型 | Nullable | 説明 |
| --- | --- | --- | --- |
| `raw_event_id` | string | no | raw event の一意 ID。 |
| `event_family` | string | no | `ask_audit_json`, `followup_resolution_json` など。 |
| `schema_version` | string | yes | payload 内の schema version。 |
| `event_ts` | timestamp | no | Cloud Logging の timestamp。 |
| `event_date` | date | no | 日本時間の日付。 |
| `ingested_at` | timestamp | yes | monitor 側取り込み時刻。 |
| `trace_id` | string | yes | request trace。 |
| `request_id` | string | yes | request ID。欠落時は `trace_id` fallback。 |
| `conversation_id` | string | yes | canonical conversation key。 |
| `session_id` | string | yes | compatibility alias。 |
| `turn_id` | string | yes | turn key。 |
| `parent_turn_id` | string | yes | parent turn。 |
| `message_id` | string | yes | message key。 |
| `user_id_hash` | string | yes | 分析用匿名ユーザー ID。 |
| `mode` | string | yes | `internal`, `websearch` など。 |
| `raw_payload_json` | json/string | no | 原始 payload。 |
| `parse_status` | string | no | `ok` / `error`。 |
| `parse_error` | string | yes | parse error。 |
| `schema_valid` | bool | yes | schema validation result。 |
| `schema_validation_errors` | json/string | yes | validation error details。 |

## 6. Projection Layer

### 6.1 Core projection tables

| Projection | 目的 | 主なソース |
| --- | --- | --- |
| `monitor_answer_events` | 回答単位の品質、根拠、ルートを確認する | `ask_audit_json`, Firestore message |
| `monitor_followup_events` | 追問判定と文脈継承を確認する | `followup_resolution_json` |
| `monitor_followup_open_events` | 追問認識から成功までを確認する | `followup_open_result_json` |
| `monitor_coverage_gap_workitems` | 低カバレッジと不足資料を管理する | `coverage_gap_workitem_json`, `ask_audit_json` |
| `monitor_query_suggest_events` | query suggest の安定性と採用を確認する | `query_suggest_result`, query suggest feedback |
| `monitor_conversation_state` | 会話一覧を表示する | Firestore conversations |
| `monitor_message_state` | メッセージ一覧とチャット記録確認を表示する | Firestore messages |
| `monitor_chat_sync_events` | 同期・復元・mobile rollback を確認する | `chat_sync_telemetry` |

### 6.2 Answer projection

`monitor_answer_events` は `回答品質`, `ダッシュボード`, `ユーザー詳細`, `チャット記録確認` の主要ソースです。

| 分類 | Field |
| --- | --- |
| Identity | `event_ts`, `event_date`, `trace_id`, `request_id`, `conversation_id`, `session_id`, `turn_id`, `message_id`, `user_id_hash`, `mode` |
| Query | `query_hash`, `query_length`, `query_lang` |
| Routing | `route_path`, `channel_plan_primary`, `final_channel_mix_dominant`, `web_channel_used`, `structured_led` |
| Evidence | `citation_count`, `evidence_doc_count`, `evidence_structured_count`, `coverage_score`, `evidence_sufficiency` |
| Quality | `answer_success`, `answerability_level`, `usability_level`, `delivery_readiness`, `primary_reason_code` |
| Risk | `low_coverage_flag`, `bad_feedback_flag`, `error_flag`, `clarification_required`, `source_date_leaked_into_body` |

### 6.3 Message projection

`monitor_message_state` は管理者が確認するチャット記録の最小単位です。

| 分類 | Field |
| --- | --- |
| 表示 | `timestamp_jst`, `role`, `status`, `mode_at_send`, `device_class`, `content_preview`, `feedback` |
| Join | `conversation_id`, `trace_id`, `request_id`, `turn_id`, `message_id`, `user_id_hash` |
| 会話 | `chat_flow_type`, `client_origin`, `conversation_id_at_send` |
| タグ | `answer_success_tag`, `low_coverage_tag`, `bad_feedback_tag`, `followup_tag`, `error_tag`, `needs_review_tag` |

## 7. Aggregate Layer

### 7.1 `monitor_system_daily`

ダッシュボード全体 KPI と日次推移のソースです。

| Field | 用途 |
| --- | --- |
| `date` | 日本時間の日付 |
| `active_user_count` | アクティブユーザー数 |
| `message_count` | メッセージ数 |
| `answer_success_rate` | 回答成功率 |
| `low_coverage_rate` | 低カバレッジ率 |
| `error_rate` | エラー率 |
| `p95_latency_ms` | P95応答時間 |
| `structured_led_rate` | 構造化データ主導率 |
| `websearch_rate` | Web検索モード利用率 |
| `followup_success_rate` | 追問成功率 |

### 7.2 `monitor_user_daily`

ユーザー監視一覧とユーザー詳細のソースです。

| Field | 用途 |
| --- | --- |
| `date` | 日本時間の日付 |
| `user_id_hash` | 匿名化ユーザー ID |
| `user_id` | 管理者権限表示用 |
| `user_email` | 管理者権限表示用 |
| `active_flag` | 当日の利用有無 |
| `message_count` | 当日のメッセージ数 |
| `answer_success_rate` | 当日の回答成功率 |
| `low_coverage_rate` | 当日の低カバレッジ率 |
| `bad_feedback_rate` | 当日の低評価率 |
| `followup_count` | 当日の追問数 |
| `error_count` | 当日のエラー数 |

### 7.3 `monitor_schema_quality_daily`

`データ健全性` のソースです。

| Field | 用途 |
| --- | --- |
| `date` | 日本時間の日付 |
| `event_family` | event family |
| `schema_version` | schema version |
| `event_count` | event 数 |
| `required_field_missing_count` | 必須項目欠落数 |
| `schema_mismatch_count` | schema 不一致数 |
| `join_rate` | 主要 join 成功率 |
| `data_delay_p95_sec` | 取り込み遅延 P95 |

## 8. Join 設計

| Join | 用途 | 備考 |
| --- | --- | --- |
| `conversation_id + turn_id` | ask / follow-up / answer の turn 単位 join | 最重要 join。 |
| `conversation_id + message_id` | answer と Firestore message の join | message-level 表示に使用。 |
| `trace_id + request_id` | request log と monitor event の join | latency / device / route 連携に使用。 |
| `user_id_hash + event_date` | user daily aggregate | 分析・一覧の匿名キー。 |
| `user_id / user_email` | 管理者検索 | 権限制御対象。 |

`session_id` は compatibility alias として保持しますが、長期的な canonical key は `conversation_id` とします。

## 9. Payload Chain

`チャット記録確認` では、以下の順に payload chain を構成できる必要があります。

```text
Firestore message
-> request_user_metric_json
-> ask_audit_json
-> followup_resolution_json
-> followup_open_result_json
-> coverage_gap_workitem_json
-> request log
```

フロントエンドの第一階層では message-level chain のみを表示します。詳細 payload は BigQuery / export に保持します。

## 10. 個人情報と本文管理

| データ | 基本方針 |
| --- | --- |
| `user_id` | 管理者画面では表示可能。分析主キーには `user_id_hash` を使う。 |
| `user_email` | 管理者画面では検索・表示可能。export では匿名化選択を用意する。 |
| `content` | 前端一覧では preview を基本とする。全文表示と export は管理者選択にする。 |
| `raw_payload_json` | 通常画面には表示しない。BigQuery / export / restricted debug で保持する。 |
| `trace_id`, `request_id`, `turn_id` | 調査用 ID として表示・export 可能。 |

## 11. 保存期間

| データ | 方針 |
| --- | --- |
| BigQuery raw / projection | 既定 180日。 |
| Firestore chat data | 既存 chat retention に従う。 |
| Export output | 一時生成を原則とし、永続保存しない。 |
| Aggregates | Raw より長期保持する場合は個人情報を含めない。 |

## 12. フロントエンド連携

フロントエンドは BigQuery に直接依存せず、API payload を通じて必要な情報を取得します。

| 画面 | 主な API | 主なデータ |
| --- | --- | --- |
| `ダッシュボード` | `/api/metrics/system-dashboard` | KPI、利用推移、活性度分布、環境・モード、品質、追問 |
| `回答品質` | `/api/metrics/answer-quality` | 回答品質分布、低カバレッジ、品質理由 |
| `追問分析` | `/api/metrics/followup` | 追問認識、成功率、訂正、確認要求 |
| `ユーザー監視` | `/api/metrics/users` | ユーザー一覧、絞り込み、検索 |
| `ユーザー詳細` | `/api/metrics/users/{user_id}` | 単一ユーザー集計、会話一覧 |
| `チャット記録確認` | `/api/trace/messages` | 会話一覧、メッセージ一覧、軽量タグ |
| `データ健全性` | `/api/metrics/schema-health` | schema / join / delay |

## 13. MVP 実装順序

1. Raw / projection view を定義する。
2. `monitor_system_daily` と `monitor_user_daily` 相当の aggregate を作る。
3. フロントエンド用 API payload を固定する。
4. `ユーザー監視` と `チャット記録確認` の join を優先実装する。
5. ダッシュボードと各分析画面を新 UI に接続する。

