# OurA Navi Monitor

`lcs-rag-app` 向けの独立監視・運用コンソールです。

本プロジェクトは製品リポジトリから意図的に分離しており、以下の運用監視に特化しています。

- 利用状況およびアクティビティ監視
- エラーおよび可用性監視
- PC / モバイルのトラフィックおよび信頼性の分離分析
- Query Suggest 品質トラッキング
- 管理者限定の会話 / メッセージ詳細確認
- 主要パネルごとの CSV エクスポート
- ユーザー / セッション / メッセージ検索導線

## 1. アーキテクチャ

データプレーン:

1. `lcs-rag-app` の Cloud Run ログ
2. Logging Sink により BigQuery データセット `oura_navi_monitor` へ転送
3. FastAPI モニターサービスが BigQuery + Firestore を参照
4. 独立フロントエンド（`frontend/`）がチャート / 検索 / エクスポートを描画し、`/dashboard` で提供

コード構成:

- `app/`: API、認証、データサービス
- `frontend/`: 独立ダッシュボード UI（HTML/CSS/JS + Chart.js）
- `frontend/vendor/`: ローカル同梱のサードパーティ資産（CDN 非依存）
- `deploy/`: 環境 YAML および Cloud Run サービスマニフェスト
- `scripts/`: 初期化、アラート、デプロイ自動化

セキュリティ:

- IAP ベース管理者識別（`x-goog-authenticated-user-email`）
- 厳格なメール許可リスト制御
- 開発時のみ利用可能なローカル fallback ヘッダ認証
- 任意の CORS 許可リスト（`MONITOR_CORS_ALLOWED_ORIGINS`）
- レスポンス保護ヘッダ（`nosniff`, `SAMEORIGIN`, `Referrer-Policy`, `Permissions-Policy`）

## 2. 確認済みランタイム基準

- Project: `lcs-developer-483404`
- ソースサービス: `lcs-rag-app`
- Region: `us-central1`
- Runtime SA: `lcs-agent@lcs-developer-483404.iam.gserviceaccount.com`
- Firestore Database: `lcs-user-data`
- 管理者許可リスト:
  - `2401145@tc.terumo.co.jp`
  - `2304371@tc.terumo.co.jp`
  - `0800781@tc.terumo.co.jp`
- 保持期間: `180` 日
- メッセージ全文閲覧: 設計上有効（管理者限定）

## 3. API サーフェス

### Health

- `GET /api/health`

### Metrics

- `GET /api/metrics/overview?days=7`
- `GET /api/metrics/usage?days=30`
- `GET /api/metrics/errors?days=7`
- `GET /api/metrics/devices?days=7`
- `GET /api/metrics/query-suggest?days=7`

### History Drilldown

- `GET /api/history/users?limit=50&q=...`
- `GET /api/history/users/{user_id}/conversations?include_hidden=false&limit=200&q=...`
- `GET /api/history/users/{user_id}/conversations/{conversation_id}?limit=500`

### UI

- `GET /dashboard`（新 UI）
- `GET /ops`（リダイレクト）
- `GET /ops-legacy`（旧静的ページ）

### Export CSV

- `GET /api/export/usage.csv?days=30`
- `GET /api/export/errors/trend.csv?days=7`
- `GET /api/export/errors/endpoints.csv?days=7`
- `GET /api/export/errors/types.csv?days=7`
- `GET /api/export/devices.csv?days=7`
- `GET /api/export/query-suggest/stages.csv?days=7`
- `GET /api/export/query-suggest/fallbacks.csv?days=7`
- `GET /api/export/query-suggest/facts.csv?days=7`
- `GET /api/export/users.csv?limit=500&q=...`
- `GET /api/export/conversations.csv?user_id=...&limit=500&q=...`
- `GET /api/export/messages.csv?user_id=...&conversation_id=...&limit=2000`

`/api/metrics/*`、`/api/history/*`、`/api/export/*`、`/dashboard`、`/ops*` はすべて管理者保護対象です。

## 4. ローカル実行

```bash
cd /Users/lee/Downloads/VScode/oura_navi_monitor
cp .env.example .env
# 任意: ローカルヘッダ認証を利用する場合は MONITOR_ALLOW_UNVERIFIED_LOCAL=true を設定
./scripts/run_local.sh
```

アクセス先:

- `http://127.0.0.1:8080/dashboard`

ローカル fallback 認証を有効化した場合は、以下ヘッダを付与してください。

- `x-monitor-admin-email: <allowlisted email>`

フロントエンド / バックエンドのオリジンが分離される場合は、以下を設定してください。

- `MONITOR_CORS_ALLOWED_ORIGINS=https://<frontend-domain>`

Firestore がデフォルト以外のデータベースを利用する場合は、以下を一致させてください。

- `MONITOR_FIRESTORE_DATABASE=lcs-user-data`

## 4.1 エンタープライズ向けフロントエンド依存ポリシー

- チャート描画ライブラリは **セルフホスト** しています。
  - `frontend/vendor/chart.umd.min.js`
- ダッシュボードは以下ローカル資産を読み込みます。
  - `/dashboard-assets/vendor/chart.umd.min.js`
- チャート描画の実行時に外部 CDN 依存はありません。
- サードパーティ通知および固定ハッシュ:
  - `frontend/vendor/THIRD_PARTY_NOTICES.md`

## 5. GCP ブートストラップ

### 5.1 一時的 SA キーの初期化（任意）

```bash
cd /Users/lee/Downloads/VScode/oura_navi_monitor
./scripts/create_sa_key.sh
export GOOGLE_APPLICATION_CREDENTIALS="$(pwd)/credentials/lcs-rag-app.json"
```

SA キーは初期構築用途のみに限定してください。Runtime および CI/CD はキーなし運用を推奨します。

### 5.2 BigQuery データセット + Logging Sink + View + Log Metrics 作成

```bash
cd /Users/lee/Downloads/VScode/oura_navi_monitor
export RETENTION_DAYS=180
./scripts/bootstrap_gcp.sh
```

実行内容:

- データセット存在確認 / 作成
- BigQuery 保持ポリシー（`default_table_expiration`）を 180 日で適用
- 既存 Cloud Run ログテーブルに対して partition expiration を適用（該当時）
- `lcs-rag-app` から BigQuery への Logging Sink を作成 / 更新
- Sink writer 権限を付与
  - まずプロジェクト IAM 付与を試行
  - 権限不足時はデータセット単位付与に自動フォールバック
- 補助 View を作成
- アラート用ログメトリクスを作成

### 5.3 保守的メールアラートポリシー作成

```bash
cd /Users/lee/Downloads/VScode/oura_navi_monitor
./scripts/setup_alerts.sh
```

デフォルト通知先は、確認済み 3 名の管理者メールです。

通知チャネルおよびアラートポリシー作成には Monitoring IAM 権限が必要です。

## 6. Cloud Run デプロイ

```bash
cd /Users/lee/Downloads/VScode/oura_navi_monitor
./scripts/deploy_cloud_run.sh
```

デフォルトのデプロイ先サービスは `oura-navi-monitor` です（`lcs-rag-app` と分離）。

ビルドモード:

- `BUILD_MODE=auto`（デフォルト）: Cloud Build を試行し、失敗時にローカル Docker build/push へ自動フォールバック
- `BUILD_MODE=cloudbuild`: Cloud Build のみ強制
- `BUILD_MODE=docker`: ローカル Docker（`linux/amd64`）build/push を強制

本デプロイには以下が含まれます。

- Backend API（`app/`）
- 独立フロントエンド資産（`frontend/`）

代替（サービスマニフェスト方式）:

```bash
cd /Users/lee/Downloads/VScode/oura_navi_monitor
./scripts/deploy_cloud_run_yaml.sh
```

- マニフェスト: `deploy/cloudrun.service.yaml`
- Cloud Build CI/CD: `cloudbuild.yaml`

### 6.1 GitHub -> Cloud Build -> Cloud Run（手動承認ゲート）

GitHub リポジトリ:

- `https://github.com/Aoki-311/oura_navi_monitor`

本番トリガー作成 / 更新（手動承認必須）:

```bash
cd /Users/lee/Downloads/VScode/oura_navi_monitor
./scripts/create_github_trigger.sh
```

デフォルトトリガーフィルタ:

- 含まれるファイル:
  - `app/**`
  - `frontend/**`
  - `deploy/**`
  - `scripts/**`
  - `sql/**`
  - `tests/**`
  - `e2e/**`
  - `Dockerfile`
  - `requirements.txt`
  - `cloudbuild.yaml`
  - `.env.example`
- 無視されるファイル:
  - `**/.venv/**`
  - `**/__pycache__/**`
  - `**/*.pyc`
  - `**/.DS_Store`
  - `docs/**`
  - `**/*.md`

トリガー挙動:

- `main` への push で build 開始
- 承認者が手動承認するまで `PENDING` 状態で待機
- 承認後に Cloud Build が Cloud Run へデプロイ

保留中 build の承認 / 却下:

```bash
cd /Users/lee/Downloads/VScode/oura_navi_monitor
./scripts/approve_pending_build.sh                # pending 一覧
./scripts/approve_pending_build.sh <BUILD_ID>     # 承認
./scripts/approve_pending_build.sh <BUILD_ID> reject
```

`Repository mapping does not exist` が出る場合は、Cloud Build UI で管理者アカウントにより一度リポジトリ接続を実施してください。

- `https://console.cloud.google.com/cloud-build/triggers;region=global/connect?project=lcs-developer-483404`

本プロジェクト推奨リージョン: `us-central1`

## 7. 保守的アラート初期基準

初期基準として以下を実装済みです。

- 5xx 高頻度: 10 分で `>= 3`
- query-suggest degraded 高頻度: 30 分で `>= 15`
- restore_failed 高頻度: 30 分で `>= 3`

アラート比較演算子は、しきい値に合わせて `COMPARISON_GE` を設定しています。

次段階推奨（イベント成熟後に追加）:

- 比率ベースアラート:
  - degraded/total > 35%
  - restore_failed/(restore_success+restore_empty+restore_failed) > 10%
- パス単位の P95 アラート

## 8. 既知のデータギャップ

現行プロダクトログでは、一部の目標指標に対する可観測性が限定的です。

現時点で直接取得可能:

- リクエスト数、5xx、レイテンシ、UA 分類
- backend ログ由来 query_suggest_result（stage/stability/latency）
- backend ログ由来 query_suggest degraded fallback source
- 同期系テレメトリイベント（`restore_*`, `pull_*`）
- Firestore 会話 / メッセージ全文

イベント整備が未完了の領域:

- immutable event log ベースの query-suggest click/adoption/edit の厳密時系列

現行実装では以下を組み合わせています。

- BigQuery ログイベント
- Firestore `querySuggestRuntimeSummary.suggestionFacts` 集計

厳密な分析用ファネルを構築する場合は、ソースサービス側で immutable event table 取り込みを追加してください。

## 8.1 Cloud Log / Firestore フィールド辞書（項目名・意味・PII・保持期間・参照先）

以下の定義は現行コードおよびデプロイ既定値に基づきます。

- Cloud Run ログ監視の主参照先: BigQuery データセット `oura_navi_monitor`（Logging Sink）
- BigQuery ログ保持: `MONITOR_RETENTION_DAYS=180`（既定）
- Firestore チャット TTL: `CHAT_RETENTION_DAYS=90`（お気に入り会話は `expireAt` 未設定可）
- Firestore hidden 会話保持: `CHAT_HIDDEN_RETENTION_DAYS=365`
- 注記: Firestore TTL は GCP 側で TTL フィールドルール有効化後に適用されます

### A. Cloud Log（構造化リクエストログ、BigQuery: `run_googleapis_com_requests`）

| フィールド名 | 意味 | PII | 保持期間 | 参照先 |
| --- | --- | --- | --- | --- |
| `timestamp` | リクエスト発生時刻（UTC） | いいえ | 180日（BigQuery Sink） | BigQuery `run_googleapis_com_requests` |
| `resource.type` | リソース種別（固定 `cloud_run_revision`） | いいえ | 同左 | BigQuery / Logging Explorer |
| `resource.labels.service_name` | Cloud Run サービス名（例: `lcs-rag-app`） | いいえ | 同左 | BigQuery / Logging Explorer |
| `httpRequest.requestMethod` | HTTP メソッド（GET/POST 等） | いいえ | 同左 | BigQuery |
| `httpRequest.requestUrl` | 完全 URL（path + query） | 低（業務パラメータ混入の可能性） | 同左 | BigQuery |
| `path(派生)` | `requestUrl` から抽出した API パス（例: `/v2/ask`） | いいえ | 同左 | Monitor API（`/api/metrics/*`） |
| `httpRequest.status` | レスポンスステータス | いいえ | 同左 | BigQuery / Monitor API |
| `httpRequest.latency` | リクエスト遅延（原文） | いいえ | 同左 | BigQuery |
| `latency_ms(派生)` | 遅延ミリ秒（P95/平均用） | いいえ | 同左 | Monitor API |
| `httpRequest.userAgent` | 端末 UA（PC/モバイル分類用） | 中（端末指紋リスク） | 同左 | BigQuery / Monitor API |
| `device_class(派生)` | `desktop/mobile/unknown` | いいえ | 同左 | Monitor API（`/api/metrics/devices`,`/api/metrics/usage`） |
| `core_request_count(派生)` | コア業務リクエスト数（`/v2/ask`,`/v2/conversations*`） | いいえ | 同左 | Monitor API（`overview`,`usage`） |
| `system_request_count(派生)` | システム系リクエスト数（非コア） | いいえ | 同左 | Monitor API（`overview`,`usage`） |

### B. Cloud Log（アプリ標準出力ログ、BigQuery: `run_googleapis_com_stdout`/`stderr`）

| フィールド名 | 意味 | PII | 保持期間 | 参照先 |
| --- | --- | --- | --- | --- |
| `textPayload` | アプリ出力ログ本文 | 可能性あり（ログ内容依存） | 180日（BigQuery Sink） | BigQuery `run_googleapis_com_stdout/stderr` |
| `query_suggest_result.stage` | 入力予測ステージ（`stable/degraded`） | いいえ | 同左 | `/api/metrics/query-suggest` |
| `query_suggest_result.latency_ms` | 入力予測レイテンシ | いいえ | 同左 | `/api/metrics/query-suggest`,`/api/metrics/overview` |
| `query_suggest_result.suggestion_count` | 1 回の候補返却数 | いいえ | 同左 | 同左 |
| `query_suggest_refine_degraded.fallback` | degraded 時 fallback ソース | いいえ | 同左 | `/api/metrics/query-suggest` |
| `query_suggest_refine_degraded.reason` | degraded 理由 | いいえ | 同左 | 同左 |
| `chat_sync_telemetry.event` | 履歴復元 / 同期イベント（`restore_*` 等） | いいえ | 同左 | `/api/metrics/overview` |
| `chat_sync_telemetry.user_id` | ユーザー識別子（subject） | はい | 同左 | BigQuery（管理者限定推奨） |
| `chat_sync_telemetry.conversation_id` | 会話 ID | 中 | 同左 | BigQuery |
| `ask_audit_json.trace_id` | リクエスト追跡 ID | いいえ | 同左 | Logging Explorer / BigQuery |
| `ask_audit_json.query_hash` | query ハッシュ（平文ではない） | 低 | 同左 | Logging Explorer / BigQuery |
| `ask_audit_json.intent` | 意図判定結果 | いいえ | 同左 | 同左 |
| `ask_audit_json.hit_count` | 検索ヒット数 | いいえ | 同左 | 同左 |
| `ask_audit_json.stores_queried` | 参照検索ソース集合 | いいえ | 同左 | 同左 |
| `web_mode_direct_dispatch` | Web モード分岐ルーティングイベント | いいえ | 同左 | Logging Explorer |

### C. Firestore（チャット主データ、DB: `chat_users`）

#### C-1. User Root: `chat_users/{userId}`

| フィールド名 | 意味 | PII | 保持期間 | 参照先 |
| --- | --- | --- | --- | --- |
| `userId`（ドキュメント ID） | ユーザー一意識別（通常 subject） | はい | アカウントライフサイクル準拠 | Firestore Console / `/api/history/users` |
| `userEmail` | ユーザーメール | はい | 同左 | 同左 |
| `subject` | IAP subject | はい | 同左 | 同左 |
| `identitySource` | 認証ソース（IAP/header） | 中 | 同左 | 同左 |
| `identityVerified` | 認証検証結果 | いいえ | 同左 | 同左 |
| `activeConversationId` | 現在アクティブ会話 ID | 中 | 同左 | Firestore Console |
| `updatedAt` | 最終活動時刻 | 中 | 同左 | Firestore / `/api/history/users` |
| `lastSeenAt` | 直近可視活動時刻 | 中 | 同左 | 同左 |

#### C-2. Conversation: `chat_users/{userId}/conversations/{conversationId}`

| フィールド名 | 意味 | PII | 保持期間 | 参照先 |
| --- | --- | --- | --- | --- |
| `id` | 会話 ID | 中 | アクティブ会話 90 日 TTL（お気に入りは長期可） | Firestore / `/api/history/users/{userId}/conversations` |
| `title` | 会話タイトル | 可能性あり（ユーザー入力由来） | 同左 | 同左 |
| `titleSource` | タイトル生成元（`auto/manual`） | いいえ | 同左 | 同左 |
| `mode` | 会話既定モード（`internal/websearch/...`） | いいえ | 同左 | 同左 |
| `visibility` | `active/hidden` | いいえ | hidden は既定 365 日 | 同左 |
| `deletedAt` | 論理削除時刻 | 中 | hidden 既定 365 日 | 同左 |
| `deletedBy` | 削除実行者 | はい | 同左 | Firestore Console |
| `deleteReason` | 削除理由 | 可能性あり | 同左 | Firestore Console |
| `hiddenExpireAt` | hidden データ失効時刻 | いいえ | 到期削除 | Firestore Console |
| `expireAt` | TTL 失効時刻 | いいえ | 到期削除 | Firestore Console |
| `createdAt` | 作成時刻 | いいえ | 会話準拠 | 同左 |
| `updatedAt` | 更新時刻 | いいえ | 会話準拠 | 同左 |
| `isFavorite` | お気に入り有無 | いいえ | お気に入りは TTL 未設定可 | 同左 |
| `pinnedAt` | ピン留め時刻 | いいえ | 会話準拠 | 同左 |
| `lastMessagePreview` | 最終メッセージ要約 | 可能性あり（本文要約） | 会話準拠 | 同左 |
| `messageCount` | メッセージ数 | いいえ | 会話準拠 | 同左 |
| `integrityState` | 整合性状態（`ok/empty/empty_shell/unknown`） | いいえ | 会話準拠 | 同左 |
| `revision` | 会話リビジョン | いいえ | 会話準拠 | 同左 |
| `syncToken` | 同期トークン | いいえ | 会話準拠 | 同左 |
| `querySuggestRuntimeSummary` | 入力予測集計スナップショット | 可能性あり（提案文を含む） | 会話準拠 | Firestore Console |
| `followupRuntimeSummary` | 連続追問状態サマリ | 可能性あり（要約含む） | 会話準拠 | Firestore Console |

#### C-3. Message: `chat_users/{userId}/conversations/{conversationId}/messages/{messageId}`

| フィールド名 | 意味 | PII | 保持期間 | 参照先 |
| --- | --- | --- | --- | --- |
| `id` | メッセージ ID | 中 | 既定 90 日 TTL | Firestore / `/api/history/.../{conversationId}` |
| `role` | `user/assistant` | いいえ | 同左 | 同左 |
| `content` | メッセージ全文（ユーザー query / AI 応答） | はい（高） | 同左 | 同左（管理者限定） |
| `timestamp` | メッセージ時刻 | 中 | 同左 | 同左 |
| `status` | 生成状態（`streaming/done/error/...`） | いいえ | 同左 | 同左 |
| `errorMessage` | エラー情報 | 可能性あり | 同左 | 同左 |
| `feedback` | ユーザーフィードバック（`good/bad/none`） | 低 | 同左 | 同左 |
| `grounded` | 引用 / 根拠構造 | 可能性あり | 同左 | Firestore Console |
| `attachmentNames` | 添付ファイル名 | 可能性あり | 同左 | 同左 |
| `attachmentFileIds` | 添付ファイル ID | 中 | 同左 | 同左 |
| `modeAtSend` | 送信時モード | いいえ | 同左 | Firestore Console |
| `chatFlowType` | `new_chat/continued_chat` | いいえ | 同左 | Firestore Console |
| `conversationIdAtSend` | 送信時会話 ID | 中 | 同左 | Firestore Console |
| `turnId` | 現在ターン ID | いいえ | 同左 | Firestore Console |
| `parentTurnId` | 親ターン ID（追問チェーン） | いいえ | 同左 | Firestore Console |
| `clientOrigin` | クライアント起点情報 | 低 | 同左 | Firestore Console |
| `syncToken` | メッセージ同期トークン | いいえ | 同左 | Firestore Console |
| `expireAt` | TTL 失効時刻 | いいえ | 到期削除 | Firestore Console |

#### C-4. Runtime（会話ランタイム状態）

| フィールド名 | 意味 | PII | 保持期間 | 参照先 |
| --- | --- | --- | --- | --- |
| `runtime/query_suggest.entries[].payload.suggestions[].text` | 候補テキスト | はい（製品語彙・ユーザー意図を含む可能性） | 会話準拠 | Firestore Console |
| `runtime/query_suggest.entries[].payload.meta.stage` | 候補ステージ（stable/degraded） | いいえ | 会話準拠 | Firestore Console |
| `runtime/query_suggest.feedbackProfile.*` | 表示 / クリック / 採用の集計 | いいえ | 会話準拠 | Firestore / 監視集計 |
| `runtime/query_suggest.suggestionFacts[]` | 学習事実（impression/click/adoption/edit） | 低-中 | 会話準拠 | Firestore / `/api/metrics/query-suggest`（集計） |
| `runtime/followup.snapshots[]` | 連続追問スナップショット（要約、エンティティ、facet） | 可能性あり | 会話準拠 | Firestore Console |
| `runtime/followup.lastPlan.*` | 最新追問プラン（anchor/query/policy） | 可能性あり | 会話準拠 | Firestore Console |

### D. 推奨参照導線

| 目的 | 推奨参照先 |
| --- | --- |
| リクエスト量 / エラー率 / P95 / PC・モバイル比較 | `/api/metrics/overview`,`/api/metrics/usage`,`/api/metrics/devices` |
| query-suggest 安定率 / degraded fallback | `/api/metrics/query-suggest` |
| ユーザー・会話・メッセージ全文調査 | `/api/history/users` -> `/api/history/users/{userId}/conversations` -> `/api/history/users/{userId}/conversations/{conversationId}` |
| 原始ログ深掘り | BigQuery `run_googleapis_com_requests/stdout/stderr` または Cloud Logging Explorer |

## 9. 推奨運用ポリシー

- モニターサービスは本番データソースに対して分離・読み取り専用を維持する
- SA キーを Runtime に持ち込まない。初期化後はローテーション・削除する
- 許可リスト管理者のみにアクセスを限定する
- モニターへのアクセスおよびメッセージ詳細参照履歴を Cloud Logging で監査する

## 10. ブラウザ E2E ガードレール（チャート安定性）

本プロジェクトでは、以下を対象とした Playwright ハーネスを提供しています。

- `リクエスト推移（PC / モバイル）` の長時間リフレッシュ
- Chart インスタンスリーク防止
- レイアウト増殖（ページ高さ暴走）退行防止

ローカル実行（ローカル backend を自動起動）:

```bash
cd /Users/lee/Downloads/VScode/oura_navi_monitor
./scripts/run_e2e_chart_stability.sh
```

デプロイ済み URL への実行:

```bash
cd /Users/lee/Downloads/VScode/oura_navi_monitor
MONITOR_E2E_BASE_URL="https://oura-navi-monitor-643644246736.us-central1.run.app" \
MONITOR_E2E_ADMIN_EMAIL="2401145@tc.terumo.co.jp" \
./scripts/run_e2e_chart_stability.sh
```

関連ファイル:

- `e2e/playwright.config.js`
- `e2e/tests/chart-stability.spec.js`
- `scripts/run_e2e_chart_stability.sh`
