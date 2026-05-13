# 監視システム実装ギャップ分析

## 1. 文書目的

本書は、以下の設計文書で定義した監視 UI / データ / 指標 / API の目標状態に対して、現在の `oura_navi_monitor` 実装との差分を整理し、開発タスクへ直接転換できる形でまとめる。

- `MONITOR_FRONTEND_INFORMATION_ARCHITECTURE.md`
- `MONITOR_DATA_ARCHITECTURE.md`
- `MONITOR_METRIC_CONTRACT.md`
- `MONITOR_API_CONTRACT.md`

対象は、システム全体監視、回答品質、追問分析、ユーザー監視、チャット記録確認、エクスポート、データ健全性である。細かい UI 実装は後続工程で行うが、本書では「どのコードをどの目的で変更するか」を固定する。

## 2. 現在コードの前提

| 領域 | 現在の実装 | 主なファイル |
|---|---|---|
| メトリクス API | 旧ダッシュボード向けに `/api/metrics/dashboard`、`/overview`、`/usage`、`/errors`、`/devices`、`/query-suggest` を提供している。新契約の `/system-dashboard`、`/answer-quality`、`/followup`、`/users`、`/schema-health` は未実装。 | `app/routers/metrics.py` |
| BigQuery 集計 | Cloud Run request、query suggest、sync telemetry、follow-up open、request user metric の一部を集計している。`ask_audit_json`、`followup_resolution_json`、`coverage_gap_workitem_json`、schema health は未展開。 | `app/services/bigquery_metrics.py`, `sql/create_views.sql` |
| Firestore 履歴 | ユーザー、会話、メッセージの一覧・詳細・CSV 出力は存在する。ユーザー監視用の活性度、直近7日利用日数、低カバレッジ、回答成功率との join は未実装。 | `app/routers/history.py`, `app/services/firestore_history.py` |
| チャット記録検索 | ユーザー起点で会話とメッセージを辿れるが、`conversation_id` / `trace_id` / `turn_id` / `user_id` / `user_email` 横断検索は未実装。 | `app/routers/history.py`, `app/services/firestore_history.py` |
| エクスポート | 既存 CSV GET API が複数ある。設計済みの `POST /api/export/jobs`、期間・項目・個人情報・本文含有の選択式エクスポートは未実装。 | `app/routers/export.py` |
| フロントエンド | 旧構成の単一 HTML/JS/CSS ダッシュボード。タブは `全体` と `単一ユーザー` 中心で、新ナビゲーション、KPI 定義、ユーザー監視一覧、チャット記録確認は未反映。 | `frontend/index.html`, `frontend/app.js`, `frontend/styles.css` |
| テスト | セキュリティ UI ガードと時間窓テストのみ。新指標・API payload・エクスポート・検索のテストは未整備。 | `tests/test_security_and_ui_guardrails.py`, `tests/test_time_window.py` |

## 3. 全体ギャップ一覧

| 優先度 | 目標 | 現在コード現状 | 必要な改造 |
|---|---|---|---|
| P0 | 設計文書と同じ API 契約を提供する | 旧 `/api/metrics/dashboard` に集約されている | 新 API ルートを追加し、旧 API は互換用途として残す |
| P0 | `回答成功率` と `低カバレッジ率` を算出する | feedback、citation coverage の一部のみ | `ask_audit_json`、message feedback、coverage gap、evidence/citation を join する |
| P0 | ユーザー監視一覧を表示できる | 履歴一覧はあるが監視列が不足 | ユーザー別集計 API を追加し、活性度定義を共通関数化する |
| P0 | チャット記録を横断検索できる | ユーザー配下の会話参照のみ | `conversation_id` / `trace_id` / `turn_id` / `user_id` / `user_email` 検索 API を追加する |
| P0 | 管理者向け日本語 UI に刷新する | 旧 dashboard UI | ナビ、KPI、グラフ、ユーザー監視、記録確認画面を再構成する |
| P1 | 追問分析を契約通り表示する | follow-up open の recognized/success 一部のみ | `followup_resolution_json` を展開し、補正・澄清・理由シグナルを集計する |
| P1 | エクスポートを選択式にする | 固定 CSV GET API のみ | `POST /api/export/jobs` とエクスポート設定 modal を追加する |
| P1 | データ健全性を監視する | schema mismatch / join health は未実装 | raw event schema validation と join health 集計を追加する |
| P2 | BigQuery view を投影層として整備する | request/query-suggest/sync の view のみ | ask/followup/coverage/user/message projection view を追加する |

## 4. データ層ギャップ

### 4.1 目標

`raw_monitor_events` を入口に、answer、follow-up、coverage gap、message、user daily、system daily、schema health へ投影・集計する。フロントエンドは直接 raw payload を読まず、API payload 経由で利用する。

### 4.2 現在

`sql/create_views.sql` は以下の view のみを作成している。

- `v_requests`
- `v_query_suggest_results`
- `v_query_suggest_degraded`
- `v_sync_telemetry`

`app/services/bigquery_metrics.py` はこれらに加え、stdout の JSON 文字列から `followup_open_result_json` と `request_user_metric_json` を個別クエリで抽出している。

### 4.3 改造タスク

| 優先度 | タスク | 変更対象 |
|---|---|---|
| P0 | `ask_audit_json` を抽出し、回答品質・低カバレッジに必要な最小字段を集計できるようにする | `app/services/bigquery_metrics.py` |
| P0 | `coverage_gap_workitem_json` を抽出し、gap count、gap kind、latest event を集計できるようにする | `app/services/bigquery_metrics.py` |
| P1 | `followup_resolution_json` を抽出し、decision、state_action、reason_signals、offtopic を集計できるようにする | `app/services/bigquery_metrics.py` |
| P1 | `schema_version`、required field missing、join status を日次集計する | `app/services/bigquery_metrics.py` |
| P2 | SQL view として `v_ask_audit_events`、`v_followup_resolution_events`、`v_coverage_gap_events` を追加する | `sql/create_views.sql` |

初期実装では Python 側クエリで直接 JSON 抽出してよい。ただし、クエリが安定した段階で view 化する。

## 5. 指標契約ギャップ

### 5.1 KPI

| 表示名 | 目標口径 | 現在コード | 必要な改造 |
|---|---|---|---|
| `アクティブユーザー数` | 選択期間に1回以上ユーザー発話がある一意ユーザー数 | `dau` / `wau` / `activeUsersInWindow` はあるが口径が旧 UI 用 | user message 起点の一意ユーザー集計へ整理 |
| `回答成功率` | エラー表示なく、再生成・回答強化・修正要求・bad feedback が見受けられない回答率 | 直接指標なし | message status、feedback、enhance、follow-up correction、error を join して算出 |
| `低カバレッジ率` | coverage gap、citation 0、evidence insufficient、coverage score 低値を含む割合 | citation coverage のみ | `ask_audit_json` と `coverage_gap_workitem_json` を集計 |
| `エラー率` | 選択期間の error/aborted/5xx 率 | 5xx と message failure は別々に存在 | API 表示用に統一値と内訳を返す |
| `P95応答時間` | 選択期間の P95 応答時間 | `requestP95LatencyMs` は存在 | 新 API payload 名へ移行 |

### 5.2 回答品質

| 目標 | 現在コード | 必要な改造 |
|---|---|---|
| 可回答性・可用性・交付可用・証拠十分性の分布 | 未実装 | ask audit / governance / survivable telemetry から抽出 |
| `verification_verdict` 分布 | 未実装 | survivable telemetry または governance を抽出 |
| `coverage_score` / `alignment_score` | 未実装 | survivable telemetry を抽出 |
| structured-led rate | 一部 source mix 未実装 | ask audit の `structured_led` を集計 |
| citation binding 異常 | 未実装 | `claim_alignment_fallback`、`citation_mapping_source`、version を集計 |

### 5.3 追問分析

| 目標 | 現在コード | 必要な改造 |
|---|---|---|
| 追問認識・追問成功漏斗 | `followup_open_result_json` の recognized/success 一部のみ | `followup_open` と `followup_resolution` の両方を統合 |
| 補正・澄清 | 未実装 | `decision_normalized` を標準 enum に変換 |
| fallback_reason | 未実装 | `reason_codes` または open result の error/fallback を抽出 |
| state_action | 未実装 | suggestion meta / follow-up open から抽出 |
| reason_signals / followup_offtopic | 未実装 | resolution payload から抽出 |

## 6. API 層ギャップ

### 6.1 新規または刷新が必要な API

| API | 目標用途 | 現在 | 実装方針 |
|---|---|---|---|
| `GET /api/metrics/system-dashboard` | ダッシュボード全体 KPI と主要グラフ | 未実装 | `metrics.py` に追加し、既存 `dashboard` の一部を再利用 |
| `GET /api/metrics/answer-quality` | 回答品質画面 | 未実装 | BigQuery answer quality 集計を返す |
| `GET /api/metrics/followup` | 追問分析画面 | 未実装 | follow-up open/resolution 集計を返す |
| `GET /api/metrics/users` | ユーザー監視一覧 | 未実装 | Firestore user summary と BigQuery quality metrics を統合 |
| `GET /api/metrics/users/{user_id}` | 単一ユーザー詳細 | 未実装 | user summary、trend、mode、quality、follow-up、conversation list を返す |
| `GET /api/trace/messages` | チャット記録確認 | 未実装 | 横断検索と payload chain を返す |
| `GET /api/metrics/schema-health` | データ健全性 | 未実装 | schema / required field / join health を返す |
| `POST /api/export/jobs` | 選択式エクスポート | 未実装 | request body で期間・出力対象・字段・個人情報处理を受ける |

### 6.2 既存 API の扱い

| 既存 API | 方針 |
|---|---|
| `/api/metrics/dashboard` | 旧 UI 互換として一時維持。新 UI は使用しない。 |
| `/api/history/users` | `ユーザー監視` 用の土台として再利用可能。ただし表示列が不足。 |
| `/api/history/users/{user_id}/conversations` | 単一ユーザー詳細の会話一覧に再利用可能。 |
| `/api/history/users/{user_id}/conversations/{conversation_id}` | 会話詳細の message 取得に再利用可能。横断検索 API とは別扱い。 |
| 既存 CSV GET API | 旧互換として維持。新 UI は `POST /api/export/jobs` を優先使用。 |

## 7. ユーザー監視ギャップ

### 7.1 目標列

`ユーザー監視` 一覧は以下を表示する。

- `ユーザーID`
- `メールアドレス`
- `最終利用日時`
- `直近7日利用日数`
- `直近7日メッセージ数`
- `根拠カバレッジ率`
- `低評価率`
- `活性度区分`
- `詳細`

### 7.2 現在

Firestore 履歴から user/conversation/message を辿る機能はあるが、以下が不足している。

- `直近7日利用日数`
- `直近7日メッセージ数`
- `根拠カバレッジ率`
- `低評価率`
- 共通定義に基づく `活性度区分`
- `user_email` 検索用 index または fallback 探索

### 7.3 改造タスク

| 優先度 | タスク | 変更対象 |
|---|---|---|
| P0 | 活性度区分を共通関数化し、円グラフとユーザー表で同じ定義を使う | `app/services/firestore_history.py` または新規 utility |
| P0 | user 別に直近7日利用日数・メッセージ数・最終利用日時を返す | `app/services/firestore_history.py` |
| P0 | user 別の低評価率を message feedback から算出する | `app/services/firestore_history.py` |
| P1 | user 別の根拠カバレッジ率を BigQuery answer metrics と join する | `app/routers/metrics.py`, `app/services/bigquery_metrics.py` |
| P1 | `user_id` / `user_email` 検索を API query と UI に追加する | `app/routers/metrics.py`, `frontend/app.js` |

## 8. チャット記録確認ギャップ

### 8.1 目標

管理者が以下で検索できる。

- `conversation_id`
- `trace_id`
- `turn_id`
- `user_id`
- `user_email`
- 期間
- 状態
- モード

最低限、payload chain として以下を表示する。

- `送信日時（日本時間）`
- `役割`
- `モード`
- `デバイス`
- `状態`
- `本文`
- `Conversation ID`
- `Trace ID`
- `Turn ID`
- `User ID`

### 8.2 現在

`history.py` は user 起点の階層取得のみで、trace / turn をキーにした横断検索がない。message export には一部字段があるが、UI で trace drilldown として利用する payload は未整備。

### 8.3 改造タスク

| 優先度 | タスク | 変更対象 |
|---|---|---|
| P0 | `GET /api/trace/messages` を追加する | 新規 `app/routers/trace.py` または `app/routers/history.py` |
| P0 | message projection に `trace_id`、`request_id`、`turn_id`、`message_id`、`device_class`、`content_preview` を含める | `app/services/firestore_history.py` |
| P0 | `conversation_id` 単独検索を user 不明でも実行できるようにする | `app/services/firestore_history.py` |
| P1 | BigQuery の trace payload と Firestore message を join し、`回答成功`、`低カバレッジ`、`低評価`、`追問`、`エラー`、`要確認` タグを返す | `app/services/bigquery_metrics.py`, trace router |
| P1 | front UI に検索フォーム、message table、会話一覧 table を追加する | `frontend/index.html`, `frontend/app.js`, `frontend/styles.css` |

Firestore だけで trace / turn を完全検索できない場合は、初期実装では `user_id` または `conversation_id` 検索を主経路とし、`trace_id` / `turn_id` は BigQuery 側から候補 message を引く。

## 9. エクスポートギャップ

### 9.1 目標

`エクスポート` ボタンから modal を開き、管理者が以下を選べる。

- 期間: 今日、直近6時間、直近12時間、過去3日、過去7日、過去14日、過去30日、任意期間
- 出力対象: ユーザー監視一覧、メッセージ明細、単一ユーザー詳細
- 字段: 基本字段、品質字段、追問字段、本文、raw payload
- 個人情報处理: ハッシュのみ、メール含む、本文含む
- 形式: CSV 初期、将来 XLSX

### 9.2 現在

固定 GET CSV API が複数存在し、用途ごとに endpoint が分かれている。modal から選択式に呼び出す job API はない。

### 9.3 改造タスク

| 優先度 | タスク | 変更対象 |
|---|---|---|
| P1 | `POST /api/export/jobs` を追加する | `app/routers/export.py` |
| P1 | request body schema を定義する | 新規 model または router 内 Pydantic model |
| P1 | `user_monitoring_summary` と `message_detail` の出力を実装する | `app/services/firestore_history.py`, `app/services/bigquery_metrics.py` |
| P1 | 本文と raw payload は初期 OFF にする | `app/routers/export.py`, `frontend/app.js` |
| P2 | 大容量 export の非同期 job 化を検討する | 後続設計 |

## 10. フロントエンドギャップ

### 10.1 目標ナビゲーション

- `ダッシュボード`
- `回答品質`
- `追問分析`
- `ユーザー監視`
- `チャット記録確認`
- `データ健全性`

### 10.2 現在

`frontend/app.js` は旧 dashboard payload を前提にしており、`HELP` 定義や chart state も旧 KPI と旧 graph 構成になっている。`frontend/index.html` は新ナビゲーションと modal 構成に未対応。

### 10.3 改造タスク

| 優先度 | タスク | 変更対象 |
|---|---|---|
| P0 | 新 navigation と section skeleton を作る | `frontend/index.html` |
| P0 | KPI card を5枚に変更し、`?` 定義を非技術者向け説明に固定する | `frontend/index.html`, `frontend/app.js` |
| P0 | `system-dashboard` API に合わせて dashboard render を作り直す | `frontend/app.js` |
| P0 | ユーザー監視一覧と検索・活性度 filter を追加する | `frontend/index.html`, `frontend/app.js` |
| P0 | チャット記録確認検索と message table を追加する | `frontend/index.html`, `frontend/app.js` |
| P1 | 回答品質・追問分析・データ健全性の個別画面を追加する | `frontend/index.html`, `frontend/app.js` |
| P1 | エクスポート modal を共通 component として実装する | `frontend/index.html`, `frontend/app.js`, `frontend/styles.css` |
| P1 | 現在の chart state を新 chart key に整理する | `frontend/app.js` |

## 11. バックエンド実装タスク詳細

### 11.1 `app/services/bigquery_metrics.py`

| 優先度 | 追加/変更メソッド | 内容 |
|---|---|---|
| P0 | `get_system_dashboard_metrics(window)` | KPI、利用推移、活性度分布、時間帯別 request、device、mode、品質概要、追問概要を返す |
| P0 | `get_answer_quality_metrics(window, user_id=None)` | 回答成功率、低カバレッジ率、可回答性、可用性、交付可用、証拠十分性を返す |
| P0 | `get_coverage_gap_metrics(window, user_id=None)` | gap kind、query hash、頻度、latest occurrence、workitem 状態を返す |
| P1 | `get_followup_metrics(window, user_id=None)` | recognized、success、correction、clarification、reason/fallback/state_action を返す |
| P1 | `get_schema_health_metrics(window)` | schema mismatch、required missing、join health を返す |
| P1 | `search_trace_payloads(window, trace_id=None, turn_id=None, conversation_id=None, user_id=None)` | trace 検索補助。Firestore message と join する候補を返す |

### 11.2 `app/services/firestore_history.py`

| 優先度 | 追加/変更メソッド | 内容 |
|---|---|---|
| P0 | `list_user_monitoring_rows(window, activity_filter=None, query=None)` | ユーザー監視一覧の基礎行を返す |
| P0 | `get_user_detail_summary(user_id, window)` | 単一ユーザー詳細の summary と trend を返す |
| P0 | `search_messages(...)` | conversation/user/email/status/mode で message を検索 |
| P0 | message row normalization | JST timestamp、role、status、mode、device、content_preview、trace/request/turn/message id を標準化 |
| P1 | `build_activity_distribution(window)` | 円グラフと user table が同じ活性度定義を使う |
| P1 | `export_monitoring_rows(...)` | 選択式 export 用 rows を返す |

### 11.3 `app/routers/metrics.py`

| 優先度 | 追加 route | 内容 |
|---|---|---|
| P0 | `/api/metrics/system-dashboard` | dashboard API contract 対応 |
| P0 | `/api/metrics/answer-quality` | 回答品質 API contract 対応 |
| P0 | `/api/metrics/users` | ユーザー監視一覧 |
| P0 | `/api/metrics/users/{user_id}` | 単一ユーザー詳細 |
| P1 | `/api/metrics/followup` | 追問分析 |
| P1 | `/api/metrics/schema-health` | データ健全性 |

### 11.4 新規 `app/routers/trace.py`

| 優先度 | route | 内容 |
|---|---|---|
| P0 | `/api/trace/messages` | conversation_id / trace_id / turn_id / user_id / user_email の検索入口 |

`app/main.py` に router 登録が必要である。

### 11.5 `app/routers/export.py`

| 優先度 | 追加 route | 内容 |
|---|---|---|
| P1 | `POST /api/export/jobs` | 選択式 export を受け、CSV を返す |

初期実装では同期 CSV download とする。件数が増えた場合のみ非同期 job store を検討する。

## 12. テストギャップ

| 優先度 | テスト | 目的 |
|---|---|---|
| P0 | 指標 formula unit test | `回答成功率`、`低カバレッジ率`、`活性度区分` の誤差を防ぐ |
| P0 | API payload shape test | `MONITOR_API_CONTRACT.md` と response key を一致させる |
| P0 | trace search test | `conversation_id`、`trace_id`、`turn_id`、`user_id` 検索が期待通り返ること |
| P1 | export option test | 期間、字段選択、本文 OFF、個人情報处理が反映されること |
| P1 | frontend guardrail test | 日本語表示名、不要 KPI 非表示、raw payload 非表示を確認 |

既存の `tests/test_security_and_ui_guardrails.py` に UI ガードを追加し、指標計算は新規 `tests/test_metric_contracts.py` を追加する。

## 13. 実装順序

### Phase 1: バックエンド集計の土台

| 順番 | タスク | 完了条件 |
|---|---|---|
| 1 | `ask_audit_json`、`coverage_gap_workitem_json`、`followup_resolution_json` の抽出実装 | BigQuery service が各 payload を期間指定で取得できる |
| 2 | `回答成功率`、`低カバレッジ率`、`活性度区分` の共通計算実装 | unit test が通る |
| 3 | user monitoring rows の基礎 API 実装 | ユーザー表に必要な列が API で返る |

### Phase 2: API 契約実装

| 順番 | タスク | 完了条件 |
|---|---|---|
| 1 | `/api/metrics/system-dashboard` | KPI と dashboard chart payload が契約通り返る |
| 2 | `/api/metrics/answer-quality`、`/followup` | 個別画面 payload が契約通り返る |
| 3 | `/api/metrics/users`、`/users/{user_id}` | ユーザー監視と詳細 payload が契約通り返る |
| 4 | `/api/trace/messages` | trace drilldown 検索ができる |

### Phase 3: エクスポート

| 順番 | タスク | 完了条件 |
|---|---|---|
| 1 | `POST /api/export/jobs` | modal から選択した内容で CSV を返せる |
| 2 | 本文・個人情報・raw payload の safe default | 初期値では本文と raw payload が出力されない |
| 3 | 既存 CSV API の互換維持 | 旧 API が破壊されない |

### Phase 4: フロントエンド刷新

| 順番 | タスク | 完了条件 |
|---|---|---|
| 1 | ナビゲーションと dashboard skeleton | 新主导航が表示される |
| 2 | KPI と主要グラフ | 5 KPI、利用推移、活性度、環境・モード、品質、追問が表示される |
| 3 | ユーザー監視とユーザー詳細 | 一覧から詳細に遷移できる |
| 4 | チャット記録確認 | 指定 key で message chain を確認できる |
| 5 | エクスポート modal | 全体・単一ユーザーの export 設定が使える |

## 14. 受入基準

| 領域 | 受入条件 |
|---|---|
| 指標 | `MONITOR_METRIC_CONTRACT.md` の formula と API response が一致する |
| API | `MONITOR_API_CONTRACT.md` の key 名、型、期間指定が一致する |
| UI | 主要表示名が日本語で固定され、非技術者向けの `?` 定義が表示される |
| ユーザー監視 | 活性度円グラフとユーザー一覧の活性度区分が同一ロジックで算出される |
| チャット記録確認 | `conversation_id` / `trace_id` / `turn_id` / `user_id` / `user_email` の検索導線がある |
| エクスポート | 期間、出力対象、字段、個人情報、本文有無を選択できる |
| 安全性 | raw payload と本文は初期表示・初期 export で出さない |
| 互換性 | 既存 `/api/metrics/dashboard` と既存 CSV GET API を初期段階では壊さない |

## 15. 開発タスク一覧

| ID | 優先度 | タスク | 主担当ファイル |
|---|---|---|---|
| T01 | P0 | BigQuery から `ask_audit_json` を抽出して answer quality 集計を作る | `app/services/bigquery_metrics.py` |
| T02 | P0 | BigQuery から `coverage_gap_workitem_json` を抽出して低カバレッジ率を作る | `app/services/bigquery_metrics.py` |
| T03 | P0 | `回答成功率` formula を実装し、unit test を追加する | `app/services/bigquery_metrics.py`, `tests/test_metric_contracts.py` |
| T04 | P0 | 活性度区分を共通関数化する | `app/services/firestore_history.py` |
| T05 | P0 | `GET /api/metrics/system-dashboard` を追加する | `app/routers/metrics.py` |
| T06 | P0 | `GET /api/metrics/users` と `GET /api/metrics/users/{user_id}` を追加する | `app/routers/metrics.py` |
| T07 | P0 | `GET /api/trace/messages` を追加する | `app/routers/trace.py`, `app/main.py` |
| T08 | P0 | チャット記録用 message row を標準化する | `app/services/firestore_history.py` |
| T09 | P0 | フロントエンド主ナビと dashboard skeleton を作る | `frontend/index.html`, `frontend/styles.css` |
| T10 | P0 | KPI 5枚と主要 dashboard chart を新 API に接続する | `frontend/app.js` |
| T11 | P0 | ユーザー監視一覧を実装する | `frontend/index.html`, `frontend/app.js` |
| T12 | P0 | チャット記録確認画面を実装する | `frontend/index.html`, `frontend/app.js` |
| T13 | P1 | `followup_resolution_json` 集計を作る | `app/services/bigquery_metrics.py` |
| T14 | P1 | `GET /api/metrics/answer-quality` と `/followup` を追加する | `app/routers/metrics.py` |
| T15 | P1 | `POST /api/export/jobs` を追加する | `app/routers/export.py` |
| T16 | P1 | export modal を実装する | `frontend/index.html`, `frontend/app.js`, `frontend/styles.css` |
| T17 | P1 | schema health / join health を実装する | `app/services/bigquery_metrics.py`, `app/routers/metrics.py` |
| T18 | P1 | frontend guardrail test と API payload test を追加する | `tests/` |
| T19 | P2 | BigQuery projection view を SQL 化する | `sql/create_views.sql` |
| T20 | P2 | 大容量 export の非同期化を検討する | 後続設計 |

## 16. 実装前に確認すべきリスク

| リスク | 内容 | 対応 |
|---|---|---|
| Firestore だけでは trace 検索が不足する | `trace_id` / `turn_id` が message に常に保存されていない可能性がある | BigQuery stdout payload を補助検索に使う |
| `user_email` が monitor event に入らない | frozen schema では user hash 中心で、email は必ずしも出ない | Firestore user profile または request_user_metric_json から取得し、ない場合は空欄 |
| 回答成功率の「再生成」「回答強化」「修正要求」判定が payload に分散する | message feedback、enhancement、follow-up correction が別 event | 初期は取得可能字段で proxy 指標を作り、字段欠損を schema health に出す |
| raw payload が大きい | UI 表示・CSV 出力で重くなり、個人情報リスクもある | 初期非表示、export も opt-in にする |
| 旧 UI と新 UI の API が混在する | 移行中に表示ずれが起きる | 新 UI は新 endpoint のみ使用し、旧 endpoint は互換維持に限定する |

