# 監視基盤・フロントエンドアップグレード実行計画

## 1. 目的

本ドキュメントは、OurA Navi Monitor の監視基盤、API、フロントエンド、エクスポート機能を段階的にアップグレードするための正式な開発タスク一覧です。

対象は、非技術管理者が一画面でシステム状態、回答品質、追問状況、ユーザー利用状況、会話・メッセージ記録を安全に確認できる運用監視コンソールです。

本ドキュメントは、以下の文書を前提に実装順序を固定します。

| 文書 | 役割 |
| --- | --- |
| `MONITOR_FRONTEND_INFORMATION_ARCHITECTURE.md` | 画面構成、表示名、指標説明、エクスポート導線を定義する。 |
| `MONITOR_FRONTEND_IMPLEMENTATION_PLAN.md` | API payload を frontend view model に変換する実装方針を定義する。 |
| `MONITOR_API_CONTRACT.md` | フロントエンドが利用する API payload 契約を定義する。 |
| `MONITOR_METRIC_CONTRACT.md` | 指標計算式、正式値 / 暫定値の扱いを定義する。 |
| `MONITOR_DATA_ARCHITECTURE.md` | BigQuery、Firestore、projection、aggregate のデータ基盤を定義する。 |
| `MONITOR_IMPLEMENTATION_GAP_ANALYSIS.md` | 目標と現行実装の差分を開発タスク化する。 |

## 2. 現在の到達点

現時点では、次期フロントエンドを作る前に必要な backend/API/data layer の主要部品はすでに実装済みです。ただし、オンライン検証、export/jobs、次期 UI 実装は未完了です。

### 2.1 完了済み

| 領域 | 状態 |
| --- | --- |
| `system-dashboard` API | snapshot / aggregate / raw view fallback に対応済み。 |
| `users/{user_id}` API | ユーザー摘要、会話一覧、message lazy load 方針へ軽量化済み。 |
| `trace/messages` API | `limit`、`cursor`、`include_content=false`、preview-only 初期表示に対応済み。 |
| `monitor_answer_events` | answer success の official / proxy / mixed / unknown ステータスを持つ projection として整備済み。 |
| `monitor_user_daily` | ユーザー日次 aggregate として整備済み。 |
| `monitor_system_hourly` | dashboard 用 hourly aggregate として整備済み。 |
| `monitor_dashboard_snapshots` | 主要 preset の dashboard JSON snapshot として整備済み。 |
| 旧 dashboard 互換 | 旧 UI のため `/api/metrics/dashboard` 互換 payload を一時維持。 |
| フロントエンド情報設計 | MVP は単一 `ダッシュボード` + `ユーザー詳細` drilldown として整理済み。 |

### 2.2 未完了

| 領域 | 残タスク |
| --- | --- |
| Cloud Run 実環境検証 | 最新 revision、snapshot 命中、主要 endpoint 200、応答時間を確認する。 |
| 次期 frontend foundation | API client、adapter、view model、component 分離を実装する。 |
| 次期 dashboard UI | 固定順序の全セクションを新 API で描画する。 |
| ユーザー詳細 UI | 会話一覧、message lazy load、本文表示確認を実装する。 |
| export UI | `エクスポート設定` モーダルを実装する。 |
| export/jobs backend | UI と同一 projection / aggregate を使う非同期 export を実装する。 |
| 正式指標の継続確認 | answer action event の継続出力、official / proxy / mixed 表示を検証する。 |
| 旧 endpoint 廃止 | 新 UI 移行後に `/api/metrics/dashboard` の互換維持を終了する。 |

## 3. MVP 画面スコープ

MVP の第一階層は `ダッシュボード` に集約します。左側ナビゲーションは作りません。

ダッシュボード表示順は以下で固定します。

1. `KPIサマリー`
2. `利用環境・モード分析`
3. `利用推移`
4. `活性度分布`
5. `ユーザー一覧`
6. `回答品質分析`
7. `連続質問分析`

`ユーザー詳細` は `ユーザー一覧` の `詳細` から `/dashboard?user_id={user_id}` へ遷移します。会話・メッセージ確認は `ユーザー詳細` 内で行います。

## 4. MVP では作らないもの

以下は backend/API/data 能力として維持しますが、MVP の独立フロントエンドページにはしません。

| 対象 | MVP で作らない理由 |
| --- | --- |
| 独立 `回答品質` ページ | 回答品質はまず dashboard セクションとして十分に確認できる状態を優先する。 |
| 独立 `連続質問分析` ページ | 連続質問分析は dashboard セクションとして表示し、詳細化は後続に回す。 |
| 独立 `チャット記録確認` ページ | ユーザー詳細からの lazy load と export で要件を満たす。 |
| 独立 `データ健全性` ページ | 非技術管理者向け第一階層では情報量が多く、BigQuery / Cloud Logging / export で保持する。 |
| Evidence / Governance 詳細 drawer | 技術調査には有用だが、初期 UI には情報量が多すぎる。 |
| Workitem 管理 UI | まず coverage gap の可視化を優先し、triage workflow は後続で追加する。 |

## 5. 実行順序

### Phase 0: 現状固定と文書確定

目的: 仕様と実行順序を固定し、以後の実装判断がぶれない状態にする。

| ID | タスク | 完了条件 |
| --- | --- | --- |
| P0-1 | フロントエンド情報設計を最新方針へ更新する。 | `MONITOR_FRONTEND_INFORMATION_ARCHITECTURE.md` が単一 dashboard 方針を反映している。 |
| P0-2 | フロントエンド実装計画を追加する。 | `MONITOR_FRONTEND_IMPLEMENTATION_PLAN.md` が API client / adapter / view model 方針を定義している。 |
| P0-3 | 本実行計画を追加する。 | `MONITOR_UPGRADE_EXECUTION_PLAN.md` が実装順序と完了条件を定義している。 |
| P0-4 | 文書差分を commit する。 | 関係文書のみが commit され、無関係ファイルは含まれていない。 |

### Phase 1: Cloud Run と API のオンライン検証

目的: frontend 実装前に、接続先 API が実環境で安定していることを確認する。

| ID | タスク | 完了条件 |
| --- | --- | --- |
| P1-1 | `oura-navi-monitor` Cloud Run が最新 revision を向いているか確認する。 | traffic 100% の revision と commit / image が確認できる。 |
| P1-2 | `system-dashboard` が snapshot を優先利用しているか確認する。 | `preset=today`, `last_7d`, `last_30d` が 200 で返り、snapshot/table/view fallback が記録できる。 |
| P1-3 | 主要 API smoke を行う。 | `system-dashboard`, `users`, `users/{user_id}`, `trace/messages`, `schema-health` が 200 で返る。 |
| P1-4 | 応答時間を記録する。 | dashboard は目標 1 秒級、user detail は目標 1-2 秒級、trace/messages は pagination 付きで安定する。 |
| P1-5 | 旧 UI 互換を確認する。 | 既存 frontend が `/api/metrics/dashboard` で最低限の数字を表示できる。 |

### Phase 2: Frontend foundation

目的: 画面 component が API payload を直接読まない構造を作る。

| ID | タスク | 完了条件 |
| --- | --- | --- |
| P2-1 | 共通 API client を追加する。 | timeout、query parameter、HTTP error、IAP same-origin request を共通化する。 |
| P2-2 | 共通 formatter を追加する。 | count、rate、ms、JST datetime、nullable を一元変換する。 |
| P2-3 | label mapping を追加する。 | mode、device、role、status、activity level をビジネス日本語に変換する。 |
| P2-4 | metric status badge を追加する。 | `official`, `proxy`, `mixed`, `unknown` を `正式値`, `暫定値`, `正式値・暫定値混在`, `データなし` に変換する。 |
| P2-5 | dashboard adapter を追加する。 | `/api/metrics/system-dashboard` payload を DashboardViewModel に変換できる。 |
| P2-6 | users adapter を追加する。 | `/api/metrics/users` payload を UserListViewModel に変換できる。 |
| P2-7 | user detail adapter を追加する。 | `/api/metrics/users/{user_id}` payload を UserDetailViewModel に変換できる。 |
| P2-8 | trace messages adapter を追加する。 | `/api/trace/messages` payload を MessageChainViewModel に変換できる。 |
| P2-9 | 共通状態 component を追加する。 | loading、empty、error、partial、stale を全ページで使える。 |

### Phase 3: Dashboard UI 実装

目的: 第一階層 dashboard を新 API と新 view model で作り直す。

| ID | タスク | 完了条件 |
| --- | --- | --- |
| P3-1 | 画面 shell を作る。 | 左 nav なし、上部 toolbar、期間選択、`エクスポート` ボタンを持つ。 |
| P3-2 | `KPIサマリー` を実装する。 | 5 KPI、`?` 説明、metric status badge、stale 表示が出る。 |
| P3-3 | `利用推移` を実装する。 | アクティブユーザー数を縦棒、メッセージ数を折れ線で表示する。 |
| P3-4 | `活性度分布` を実装する。 | 円グラフ中央に `総ユーザー数` を表示し、表示期間を `3日`、`7日`、`14日`、`30日`、`全部` から選べる。 |
| P3-5 | `利用環境・モード分析` を実装する。 | 時間帯別リクエスト、利用デバイス分布、利用モード分布を同一期間で表示する。 |
| P3-6 | `回答品質分析` を実装する。 | 回答可能性、回答利用可能性、業務利用可能性、根拠十分性を表示する。 |
| P3-7 | `連続質問分析` を実装する。 | 追問認識数、追問成功率、明示的な訂正、確認が必要な追問を表示する。 |
| P3-8 | dashboard の partial failure を実装する。 | 一部データ取得失敗時に画面全体を空にしない。 |

### Phase 4: ユーザー一覧

目的: dashboard 下部で、ユーザー別の利用状況とリスクを一覧確認できるようにする。

| ID | タスク | 完了条件 |
| --- | --- | --- |
| P4-1 | `ユーザー一覧` toolbar を実装する。 | `活性度で絞り込み`、`ユーザーID / メールで検索`、`エクスポート` を表示する。 |
| P4-2 | user table を実装する。 | 指定カラム、sticky header、pagination、copy ID、empty/error state を持つ。 |
| P4-3 | 活性度定義を共通化する。 | 円グラフと user table の `活性度区分` が必ず同一定義になる。 |
| P4-4 | `詳細` 遷移を実装する。 | ユーザー row から `ユーザー詳細` へ遷移できる。 |
| P4-5 | user list performance を確認する。 | `limit`、`cursor`、検索条件付きでも画面が固まらない。 |

### Phase 5: ユーザー詳細

目的: 単一ユーザーの摘要、品質、追問、会話一覧を軽量に確認できるようにする。

| ID | タスク | 完了条件 |
| --- | --- | --- |
| P5-1 | user detail routing を実装する。 | `詳細` から user_id を引き継ぎ、戻る導線を持つ。 |
| P5-2 | user summary cards を実装する。 | メッセージ数、回答成功率、低カバレッジ率、低評価率、追問数を表示する。 |
| P5-3 | user charts を実装する。 | 利用推移、モード分布、回答品質分布、追問状況を表示する。 |
| P5-4 | 会話一覧を実装する。 | conversation_id、title、mode、visibility、created_at、updated_at、message_count、integrity_state、is_favorite、followup_runtime_summary を表示する。 |
| P5-5 | conversation pagination を実装する。 | `conversation_limit` と `conversation_cursor` を使う。 |
| P5-6 | user detail export button を実装する。 | 右上 `エクスポート` から単一ユーザー向け export dialog を開く。 |

### Phase 6: 会話・メッセージ確認

目的: ユーザー詳細から、必要な会話・メッセージ chain を安全に lazy load する。

| ID | タスク | 完了条件 |
| --- | --- | --- |
| P6-1 | 会話 row 選択時の message lazy load を実装する。 | `/api/trace/messages?conversation_id=...&user_id=...&include_content=false` を呼ぶ。 |
| P6-2 | message table を実装する。 | timestamp、role、status、mode_at_send、chat_flow_type、client_origin、feedback、content_preview、trace_id、request_id、turn_id、message_id を表示する。 |
| P6-3 | 本文表示確認を実装する。 | 初期表示では本文を取得せず、`本文を表示` 確認後に `include_content=true` で再取得する。 |
| P6-4 | message pagination を実装する。 | `limit` と `cursor` を使い、長い会話でも安定する。 |
| P6-5 | technical ID copy を実装する。 | trace_id、request_id、turn_id、message_id を短縮表示し、copy できる。 |

### Phase 7: エクスポート UI

目的: dashboard と user detail から、管理者が必要な粒度を選んで出力できるようにする。

| ID | タスク | 完了条件 |
| --- | --- | --- |
| P7-1 | `エクスポート設定` modal を実装する。 | 対象期間、出力データ、出力項目、個人情報の扱いを選択できる。 |
| P7-2 | dashboard export options を実装する。 | `ユーザー監視一覧` と `メッセージ明細` を選べる。 |
| P7-3 | user detail export options を実装する。 | 選択ユーザーの摘要、会話、メッセージ明細を選べる。 |
| P7-4 | 本文出力確認を実装する。 | `メッセージ本文` を選択した場合、個人情報・業務情報の確認文を出す。 |
| P7-5 | export disabled / pending state を実装する。 | backend jobs 未接続時でも UI が壊れず、利用可能状態を明示する。 |

### Phase 8: export/jobs backend

目的: UI と同じ projection / aggregate を使い、再計算しない export を実装する。

| ID | タスク | 完了条件 |
| --- | --- | --- |
| P8-1 | `POST /api/export/jobs` を実装する。 | preset、custom range、outputData、includedFields、personalInfoMode、filters を受け取る。 |
| P8-2 | job status を実装する。 | queued、running、completed、failed を返せる。 |
| P8-3 | `GET /api/export/jobs/{job_id}/download` を実装する。 | CSV または ZIP を download できる。 |
| P8-4 | user list export を aggregate から出力する。 | UI の `ユーザー一覧` と同じ口径になる。 |
| P8-5 | message detail export を trace/messages 系 projection から出力する。 | UI の会話・メッセージ確認と同じ口径になる。 |
| P8-6 | audit log を残す。 | 本文出力、個人情報出力、管理者ID、対象期間、出力対象を記録する。 |
| P8-7 | TTL を設定する。 | export 生成物を長期保存しない。 |

### Phase 9: デプロイと運用検証

目的: 新 UI が実運用で安定し、旧 UI 依存を外せる状態にする。

| ID | タスク | 完了条件 |
| --- | --- | --- |
| P9-1 | local test を実行する。 | `python3 -m pytest` が通る。frontend build がある場合は build も通る。 |
| P9-2 | Cloud Build を実行する。 | build 成功、approval が必要な場合は承認待ちになる。 |
| P9-3 | Cloud Run revision を確認する。 | 新 revision に traffic が切り替わっている。 |
| P9-4 | endpoint smoke を行う。 | dashboard、users、user detail、trace/messages、export jobs が 200 または期待 status を返す。 |
| P9-5 | UI smoke を行う。 | dashboard 数字表示、user detail 遷移、message preview、export modal が動く。 |
| P9-6 | Cloud Logging を確認する。 | 主要 endpoint の 2xx、エラー率、latency が確認できる。 |
| P9-7 | dashboard snapshot freshness を確認する。 | generatedAt と dataDelaySec が許容範囲内である。 |

### Phase 10: 旧 endpoint / 旧 UI の整理

目的: 新 UI 移行後に互換コードを残し続けない。

| ID | タスク | 完了条件 |
| --- | --- | --- |
| P10-1 | frontend から旧 `/api/metrics/dashboard` 参照を削除する。 | 新 UI は `/api/metrics/system-dashboard` のみを使う。 |
| P10-2 | 旧 endpoint 利用ログを確認する。 | 一定期間、旧 endpoint への frontend access がない。 |
| P10-3 | 旧 endpoint を `410 Gone` または明示 alias に変更する。 | 誤利用時に新 endpoint へ誘導できる。 |
| P10-4 | README と API docs を更新する。 | 旧 endpoint が legacy であることを明記する。 |

## 6. API 依存関係

| UI 領域 | API | 必須状態 |
| --- | --- | --- |
| `KPIサマリー` | `GET /api/metrics/system-dashboard` | snapshot fallback を含めて安定して返る。 |
| `利用推移` | `GET /api/metrics/system-dashboard` | usageTrend を返す。 |
| `活性度分布` | `GET /api/metrics/system-dashboard` | activityDistribution を返す。 |
| `利用環境・モード分析` | `GET /api/metrics/system-dashboard` | requestByHour、deviceDistribution、modeDistribution を返す。 |
| `回答品質分析` | `GET /api/metrics/system-dashboard` | answerQuality を返す。 |
| `連続質問分析` | `GET /api/metrics/system-dashboard` | followup を返す。 |
| `ユーザー一覧` | `GET /api/metrics/users` | pagination、activity filter、q search を返す。 |
| `ユーザー詳細` | `GET /api/metrics/users/{user_id}` | `include_messages=false` で軽量に返す。 |
| `会話・メッセージ確認` | `GET /api/trace/messages` | `include_content=false` 初期、cursor pagination を返す。 |
| `エクスポート` | `POST /api/export/jobs` | 後続実装。UI は先に modal と payload 生成まで作る。 |

## 7. データ依存関係

| データ | 用途 | 注意点 |
| --- | --- | --- |
| `monitor_dashboard_snapshots` | dashboard 高速表示 | 常用 preset は snapshot を優先する。 |
| `monitor_system_hourly` | dashboard fallback / 時間帯分析 | cold query でも raw log scan を避ける。 |
| `monitor_user_daily` | user list / user detail summary | ユーザー一覧と活性度分布の定義を揃える。 |
| `monitor_answer_events` | 回答成功率 / 低カバレッジ / answer quality | official / proxy / mixed を UI に伝える。 |
| `Firestore chat_users` | user profile / conversation / message body | message body は lazy load と明示確認が必須。 |
| `v_answer_action_events` | bad feedback / regenerate / enhance / correction | answer success official 口径の根拠になる。 |

## 8. 受け入れ基準

### 8.1 機能基準

| 項目 | 基準 |
| --- | --- |
| dashboard | 7セクションが固定順序で表示される。 |
| KPI | 5項目のみ表示し、各項目に `?` 説明がある。 |
| answer success | `正式値`、`暫定値`、`正式値・暫定値混在`、`データなし` を表示できる。 |
| user list | 活性度 filter、user_id / email search、pagination、詳細遷移がある。 |
| user detail | 初期表示で message body を取得しない。 |
| message chain | preview-first、本文表示確認、cursor pagination がある。 |
| export | modal で期間、出力データ、出力項目、個人情報の扱いを選択できる。 |

### 8.2 性能基準

| Endpoint / 画面 | 目標 |
| --- | --- |
| `system-dashboard` | snapshot 命中時 1 秒級。 |
| `users` | 通常検索 1-2 秒級。 |
| `users/{user_id}` | message 非取得で 1-2 秒級。 |
| `trace/messages` | 100件 pagination で安定応答。 |
| dashboard UI | 初期表示で画面全体が空白にならない。 |

### 8.3 セキュリティ / 運用基準

| 項目 | 基準 |
| --- | --- |
| message body | 初期表示・初期 API request では取得しない。 |
| include_content | 管理者確認後のみ true にする。 |
| export body | 本文出力時は確認と audit log を必須にする。 |
| PII | user_id / email / message content の export は選択式にする。 |
| stale data | snapshot 反映遅延がある場合は UI に表示する。 |

## 9. 直近の実行順

次に実行する順番は以下です。

1. 本文書を含む文書差分を commit する。
2. Cloud Run の最新 revision と主要 API smoke を確認する。
3. frontend foundation を実装する。
4. dashboard UI を新 API / adapter に切り替える。
5. `ユーザー一覧` と `ユーザー詳細` を実装する。
6. user detail 内の message lazy load を実装する。
7. `エクスポート設定` UI を実装する。
8. export/jobs backend を実装する。
9. 新 UI の online smoke を行う。
10. 新 UI 安定後に旧 `/api/metrics/dashboard` の互換維持を終了する。
