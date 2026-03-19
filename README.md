# OurA Navi Monitor

Standalone monitor and operations console for `lcs-rag-app`.

This project is intentionally isolated from the product repo and focuses on:

- Usage and activity monitoring
- Error and availability monitoring
- PC vs mobile traffic and reliability split
- Query Suggest quality tracking
- Admin-only conversation/message drilldown
- CSV export for each major panel
- User/session/message search workflow

## 1. Architecture

Data plane:

1. Cloud Run logs from `lcs-rag-app`
2. Logging Sink to BigQuery dataset `oura_navi_monitor`
3. FastAPI monitor service reads BigQuery + Firestore
4. Independent frontend (`frontend/`) renders charts/search/export and is served at `/dashboard`

Code layout:

- `app/`: API, auth, data services
- `frontend/`: independent dashboard UI (HTML/CSS/JS + Chart.js)
- `frontend/vendor/`: locally hosted third-party assets (no CDN dependency)
- `deploy/`: env yaml and Cloud Run service manifest
- `scripts/`: bootstrap, alert, deploy automation

Security:

- IAP-based admin identity check (`x-goog-authenticated-user-email`)
- Strict allowlist email gate
- Optional local fallback header gate for development only
- Optional CORS allowlist (`MONITOR_CORS_ALLOWED_ORIGINS`)
- Response hardening headers (`nosniff`, `SAMEORIGIN`, `Referrer-Policy`, `Permissions-Policy`)

## 2. Confirmed Runtime Baseline

- Project: `lcs-developer-483404`
- Source service: `lcs-rag-app`
- Region: `us-central1`
- Runtime SA: `lcs-agent@lcs-developer-483404.iam.gserviceaccount.com`
- Firestore Database: `lcs-user-data`
- Admin allowlist:
  - `2401145@tc.terumo.co.jp`
  - `2304371@tc.terumo.co.jp`
  - `0800781@tc.terumo.co.jp`
- Retention horizon: `180` days
- Full message content viewing: enabled by design (admin only)

## 3. API Surface

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

- `GET /dashboard` (new primary UI)
- `GET /ops` (redirect)
- `GET /ops-legacy` (old static page)

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

All `/api/metrics/*`, `/api/history/*`, `/api/export/*`, `/dashboard`, and `/ops*` are admin-protected.

## 4. Local Run

```bash
cd /Users/lee/Downloads/VScode/oura_navi_monitor
cp .env.example .env
# optional: set MONITOR_ALLOW_UNVERIFIED_LOCAL=true for local header auth
./scripts/run_local.sh
```

Open:

- `http://127.0.0.1:8080/dashboard`

If local fallback auth is enabled, send header:

- `x-monitor-admin-email: <allowlisted email>`

If frontend/backend are deployed on different origins, set:

- `MONITOR_CORS_ALLOWED_ORIGINS=https://<frontend-domain>`

If your Firestore uses a non-default database, keep this aligned:

- `MONITOR_FIRESTORE_DATABASE=lcs-user-data`

## 4.1 Enterprise Frontend Dependency Policy

- Chart rendering library is **self-hosted** at:
  - `frontend/vendor/chart.umd.min.js`
- Dashboard loads this local asset from:
  - `/dashboard-assets/vendor/chart.umd.min.js`
- No runtime dependency on external CDN for charts.
- Third-party notice and pinned hash:
  - `frontend/vendor/THIRD_PARTY_NOTICES.md`

## 5. GCP Bootstrap

### 5.1 Optional temporary SA key bootstrap

```bash
cd /Users/lee/Downloads/VScode/oura_navi_monitor
./scripts/create_sa_key.sh
export GOOGLE_APPLICATION_CREDENTIALS="$(pwd)/credentials/lcs-rag-app.json"
```

Use SA key only for initial provisioning. Runtime and CI/CD should be keyless.

### 5.2 Create BigQuery dataset + Logging Sink + views + log metrics

```bash
cd /Users/lee/Downloads/VScode/oura_navi_monitor
export RETENTION_DAYS=180
./scripts/bootstrap_gcp.sh
```

What this does:

- Ensures dataset exists
- Applies BigQuery retention policy (`default_table_expiration`) for 180 days
- Applies partition expiration to existing Cloud Run log tables when present
- Creates/updates Logging Sink from `lcs-rag-app` to BigQuery
- Grants sink writer permission
  - first tries project-level IAM binding
  - if caller lacks project IAM write, auto-falls back to dataset-level grant
- Creates helper views
- Creates log-based metrics for alerts

### 5.3 Create conservative email alert policies

```bash
cd /Users/lee/Downloads/VScode/oura_navi_monitor
./scripts/setup_alerts.sh
```

Default channels are the 3 confirmed admin emails.
Caller must have Monitoring IAM permission to create notification channels and alert policies.

## 6. Cloud Run Deploy

```bash
cd /Users/lee/Downloads/VScode/oura_navi_monitor
./scripts/deploy_cloud_run.sh
```

Default target service is `oura-navi-monitor` (separate from `lcs-rag-app`).
Build mode options:

- `BUILD_MODE=auto` (default): try Cloud Build, then auto-fallback to local Docker build/push
- `BUILD_MODE=cloudbuild`: force Cloud Build only
- `BUILD_MODE=docker`: force local Docker (`linux/amd64`) build/push

This deploy includes both:

- Backend API (`app/`)
- Independent frontend assets (`frontend/`)

Alternative (service manifest based):

```bash
cd /Users/lee/Downloads/VScode/oura_navi_monitor
./scripts/deploy_cloud_run_yaml.sh
```

Manifest file: `deploy/cloudrun.service.yaml`

Cloud Build CI/CD file: `cloudbuild.yaml`

### 6.1 GitHub -> Cloud Build -> Cloud Run (Manual Approval Gate)

GitHub repository:

- `https://github.com/Aoki-311/oura_navi_monitor`

Create/update the production trigger (manual approval required):

```bash
cd /Users/lee/Downloads/VScode/oura_navi_monitor
./scripts/create_github_trigger.sh
```

Default trigger file filters:

- `含まれるファイル`:
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
- `無視されるファイル`:
  - `**/.venv/**`
  - `**/__pycache__/**`
  - `**/*.pyc`
  - `**/.DS_Store`
  - `docs/**`
  - `**/*.md`

Trigger behavior:

- Push to `main` starts a build
- Build enters `PENDING` state until an approver manually approves
- After approval, Cloud Build deploys to Cloud Run

Approve/reject pending builds:

```bash
cd /Users/lee/Downloads/VScode/oura_navi_monitor
./scripts/approve_pending_build.sh                # list pending
./scripts/approve_pending_build.sh <BUILD_ID>     # approve
./scripts/approve_pending_build.sh <BUILD_ID> reject
```

If trigger creation reports `Repository mapping does not exist`, first connect the repo once in Cloud Build UI with a human admin account:

- `https://console.cloud.google.com/cloud-build/triggers;region=global/connect?project=lcs-developer-483404`

Recommended trigger region for this project: `us-central1`

## 7. Conservative Alert Baseline

Implemented as initial baseline policies:

- 5xx count high: `>= 3` in 10 min
- query-suggest degraded high: `>= 15` in 30 min
- restore_failed high: `>= 3` in 30 min

Alert comparison operators are configured as `COMPARISON_GE` to match these thresholds.

Recommended next step (can be added after event maturity):

- Ratio-based alerts:
  - degraded/total > 35%
  - restore_failed/(restore_success+restore_empty+restore_failed) > 10%
- Endpoint-level P95 alerts by path

## 8. Known Data Gaps

Current product logs have partial observability for some target metrics.

Directly available now:

- Requests, 5xx, latency, user agent split
- query_suggest_result stage/stability/latency from backend logs
- query_suggest degraded fallback source from backend logs
- sync telemetry events (`restore_*`, `pull_*`)
- Firestore conversation and message full content

Not fully event-complete yet:

- Exact query-suggest click/adoption/edit timeline from immutable event log

Current implementation combines:

- BigQuery log events
- Firestore `querySuggestRuntimeSummary.suggestionFacts` aggregates

For a strict analytics-grade funnel, add immutable event table ingestion in source service.

## 8.1 Cloud Log / Firestore 字段字典（字段名-含义-PII-保留期-查询入口）

以下口径基于当前代码与部署默认值：

- Cloud Run 日志监控主查入口：BigQuery `oura_navi_monitor` 数据集（Logging Sink 导入）
- BigQuery 日志保留：`MONITOR_RETENTION_DAYS=180`（当前默认）
- Firestore 聊天 TTL：`CHAT_RETENTION_DAYS=90`（收藏会话可不设 `expireAt`）
- Firestore 隐藏会话保留：`CHAT_HIDDEN_RETENTION_DAYS=365`
- 备注：Firestore TTL 需要在 GCP 侧已启用对应 TTL 字段规则后才会按期清理

### A. Cloud Log（请求结构化日志，BigQuery: `run_googleapis_com_requests`）

| 字段名 | 含义 | 是否 PII | 保留期 | 查询入口 |
| --- | --- | --- | --- | --- |
| `timestamp` | 请求发生时间（UTC） | 否 | 180天（BigQuery Sink） | BigQuery `run_googleapis_com_requests` |
| `resource.type` | 资源类型（固定 `cloud_run_revision`） | 否 | 同上 | BigQuery / Logging Explorer |
| `resource.labels.service_name` | Cloud Run 服务名（如 `lcs-rag-app`） | 否 | 同上 | BigQuery / Logging Explorer |
| `httpRequest.requestMethod` | HTTP 方法（GET/POST 等） | 否 | 同上 | BigQuery |
| `httpRequest.requestUrl` | 完整 URL（含 path 和 query） | 低（可能含业务参数） | 同上 | BigQuery |
| `path(派生)` | 从 `requestUrl` 抽取的接口路径（如 `/v2/ask`） | 否 | 同上 | Monitor API (`/api/metrics/*`) |
| `httpRequest.status` | 响应状态码 | 否 | 同上 | BigQuery / Monitor API |
| `httpRequest.latency` | 请求耗时（原始字符串） | 否 | 同上 | BigQuery |
| `latency_ms(派生)` | 耗时毫秒（用于 P95/均值） | 否 | 同上 | Monitor API |
| `httpRequest.userAgent` | 终端 UA（用于 PC/手机分类） | 中（设备指纹风险） | 同上 | BigQuery / Monitor API |
| `device_class(派生)` | `desktop/mobile/unknown` | 否 | 同上 | Monitor API (`/api/metrics/devices`,`/api/metrics/usage`) |
| `core_request_count(派生)` | 核心业务请求数（`/v2/ask`,`/v2/conversations*`） | 否 | 同上 | Monitor API (`overview`,`usage`) |
| `system_request_count(派生)` | 系统请求数（非核心请求） | 否 | 同上 | Monitor API (`overview`,`usage`) |

### B. Cloud Log（应用文本日志，BigQuery: `run_googleapis_com_stdout`/`stderr`）

| 字段名 | 含义 | 是否 PII | 保留期 | 查询入口 |
| --- | --- | --- | --- | --- |
| `textPayload` | 应用输出日志原文 | 可能（取决于日志内容） | 180天（BigQuery Sink） | BigQuery `run_googleapis_com_stdout/stderr` |
| `query_suggest_result.stage` | 输入预测阶段（`stable/degraded`） | 否 | 同上 | `/api/metrics/query-suggest` |
| `query_suggest_result.latency_ms` | 输入预测耗时 | 否 | 同上 | `/api/metrics/query-suggest`,`/api/metrics/overview` |
| `query_suggest_result.suggestion_count` | 单次返回候选数量 | 否 | 同上 | 同上 |
| `query_suggest_refine_degraded.fallback` | 降级时 fallback 来源 | 否 | 同上 | `/api/metrics/query-suggest` |
| `query_suggest_refine_degraded.reason` | 降级原因 | 否 | 同上 | 同上 |
| `chat_sync_telemetry.event` | 历史召回/同步事件（`restore_*` 等） | 否 | 同上 | `/api/metrics/overview` |
| `chat_sync_telemetry.user_id` | 用户标识（subject） | 是 | 同上 | BigQuery（建议仅管理员） |
| `chat_sync_telemetry.conversation_id` | 会话 ID | 中 | 同上 | BigQuery |
| `ask_audit_json.trace_id` | 请求链路追踪 ID | 否 | 同上 | Logging Explorer / BigQuery |
| `ask_audit_json.query_hash` | query 哈希（非明文） | 低 | 同上 | Logging Explorer / BigQuery |
| `ask_audit_json.intent` | 意图判定结果 | 否 | 同上 | 同上 |
| `ask_audit_json.hit_count` | 检索命中数 | 否 | 同上 | 同上 |
| `ask_audit_json.stores_queried` | 命中的检索源集合 | 否 | 同上 | 同上 |
| `web_mode_direct_dispatch` | 触发 Web 模式的路由事件 | 否 | 同上 | Logging Explorer |

### C. Firestore（聊天主数据，库：`chat_users`）

#### C-1. User Root: `chat_users/{userId}`

| 字段名 | 含义 | 是否 PII | 保留期 | 查询入口 |
| --- | --- | --- | --- | --- |
| `userId`（文档 ID） | 用户唯一标识（通常是 subject） | 是 | 跟随账号生命周期 | Firestore Console / `/api/history/users` |
| `userEmail` | 用户邮箱 | 是 | 同上 | 同上 |
| `subject` | IAP subject | 是 | 同上 | 同上 |
| `identitySource` | 身份来源（IAP/header） | 中 | 同上 | 同上 |
| `identityVerified` | 身份是否校验通过 | 否 | 同上 | 同上 |
| `activeConversationId` | 当前激活会话 ID | 中 | 同上 | Firestore Console |
| `updatedAt` | 用户最后活动时间 | 中 | 同上 | Firestore / `/api/history/users` |
| `lastSeenAt` | 最近可见活动时间 | 中 | 同上 | 同上 |

#### C-2. Conversation: `chat_users/{userId}/conversations/{conversationId}`

| 字段名 | 含义 | 是否 PII | 保留期 | 查询入口 |
| --- | --- | --- | --- | --- |
| `id` | 会话 ID | 中 | 活跃会话90天TTL（收藏可长期） | Firestore / `/api/history/users/{userId}/conversations` |
| `title` | 会话标题 | 可能（用户输入衍生） | 同上 | 同上 |
| `titleSource` | 标题来源（`auto/manual`） | 否 | 同上 | 同上 |
| `mode` | 会话默认模式（`internal/websearch/...`） | 否 | 同上 | 同上 |
| `visibility` | `active/hidden` | 否 | hidden 分支默认365天 | 同上 |
| `deletedAt` | 软删除时间 | 中 | hidden 默认365天 | 同上 |
| `deletedBy` | 删除操作者 | 是 | 同上 | Firestore Console |
| `deleteReason` | 删除原因 | 可能 | 同上 | Firestore Console |
| `hiddenExpireAt` | hidden 数据过期时间 | 否 | 到期清理 | Firestore Console |
| `expireAt` | TTL 过期时间 | 否 | 到期清理 | Firestore Console |
| `createdAt` | 创建时间 | 否 | 同会话 | 同上 |
| `updatedAt` | 更新时间 | 否 | 同会话 | 同上 |
| `isFavorite` | 是否收藏 | 否 | 收藏可不设 TTL | 同上 |
| `pinnedAt` | 置顶时间 | 否 | 同会话 | 同上 |
| `lastMessagePreview` | 最后一条消息预览 | 可能（文本摘要） | 同会话 | 同上 |
| `messageCount` | 消息数 | 否 | 同会话 | 同上 |
| `integrityState` | 数据完整性（`ok/empty/empty_shell/unknown`） | 否 | 同会话 | 同上 |
| `revision` | 会话版本号 | 否 | 同会话 | 同上 |
| `syncToken` | 同步令牌 | 否 | 同会话 | 同上 |
| `querySuggestRuntimeSummary` | 输入预测汇总快照 | 可能（含建议文本） | 同会话 | Firestore Console |
| `followupRuntimeSummary` | 连续追问状态汇总 | 可能（含摘要） | 同会话 | Firestore Console |

#### C-3. Message: `chat_users/{userId}/conversations/{conversationId}/messages/{messageId}`

| 字段名 | 含义 | 是否 PII | 保留期 | 查询入口 |
| --- | --- | --- | --- | --- |
| `id` | 消息 ID | 中 | 默认90天TTL | Firestore / `/api/history/.../{conversationId}` |
| `role` | `user/assistant` | 否 | 同上 | 同上 |
| `content` | 消息全文（用户 query / AI 回复） | 是（高） | 同上 | 同上（管理员） |
| `timestamp` | 消息时间 | 中 | 同上 | 同上 |
| `status` | 生成状态（`streaming/done/error/...`） | 否 | 同上 | 同上 |
| `errorMessage` | 错误信息 | 可能 | 同上 | 同上 |
| `feedback` | 用户反馈（`good/bad/none`） | 低 | 同上 | 同上 |
| `grounded` | 引用/证据结构 | 可能 | 同上 | Firestore Console |
| `attachmentNames` | 附件名 | 可能 | 同上 | 同上 |
| `attachmentFileIds` | 附件文件 ID | 中 | 同上 | 同上 |
| `modeAtSend` | 该条发送时实际模式 | 否 | 同上 | Firestore Console |
| `chatFlowType` | `new_chat/continued_chat` | 否 | 同上 | Firestore Console |
| `conversationIdAtSend` | 发送时会话 ID | 中 | 同上 | Firestore Console |
| `turnId` | 当前轮次 ID | 否 | 同上 | Firestore Console |
| `parentTurnId` | 父轮次 ID（追问链） | 否 | 同上 | Firestore Console |
| `clientOrigin` | 客户端来源标记 | 低 | 同上 | Firestore Console |
| `syncToken` | 消息同步令牌 | 否 | 同上 | Firestore Console |
| `expireAt` | TTL 过期时间 | 否 | 到期清理 | Firestore Console |

#### C-4. Runtime（会话运行态）

| 字段名 | 含义 | 是否 PII | 保留期 | 查询入口 |
| --- | --- | --- | --- | --- |
| `runtime/query_suggest.entries[].payload.suggestions[].text` | 候选句文本 | 是（可能含产品/用户语义） | 随会话 | Firestore Console |
| `runtime/query_suggest.entries[].payload.meta.stage` | 候选阶段（stable/degraded） | 否 | 随会话 | Firestore Console |
| `runtime/query_suggest.feedbackProfile.*` | 展现/点击/采纳聚合计数 | 否 | 随会话 | Firestore / 监控聚合 |
| `runtime/query_suggest.suggestionFacts[]` | 候选学习事实（impression/click/adoption/edit） | 低-中 | 随会话 | Firestore / `/api/metrics/query-suggest`（聚合） |
| `runtime/followup.snapshots[]` | 连续追问快照（摘要、实体、facet） | 可能 | 随会话 | Firestore Console |
| `runtime/followup.lastPlan.*` | 最近追问计划（anchor/query/policy） | 可能 | 随会话 | Firestore Console |

### D. 推荐查询入口清单

| 目标 | 推荐入口 |
| --- | --- |
| 请求量/错误率/P95/PC vs 手机 | `/api/metrics/overview`,`/api/metrics/usage`,`/api/metrics/devices` |
| query-suggest 稳定率/降级来源 | `/api/metrics/query-suggest` |
| 用户-会话-消息全文排查 | `/api/history/users` -> `/api/history/users/{userId}/conversations` -> `/api/history/users/{userId}/conversations/{conversationId}` |
| 原始日志深排查 | BigQuery `run_googleapis_com_requests/stdout/stderr` 或 Cloud Logging Explorer |

## 9. Suggested Operations Policy

- Keep monitor service isolated and read-only against production data sources.
- Keep SA key out of runtime; rotate and delete temporary key after bootstrap.
- Restrict access to allowlisted admins only.
- Audit monitor access and message drilldown usage in Cloud Logging.

## 10. Browser E2E Guardrail (Chart Stability)

This project now includes a browser-level Playwright harness that targets:

- Long refresh loops on `リクエスト推移（PC / モバイル）`
- Chart instance leak prevention
- Layout growth regression prevention (page height runaway)

Run locally (auto-starts local backend):

```bash
cd /Users/lee/Downloads/VScode/oura_navi_monitor
./scripts/run_e2e_chart_stability.sh
```

Run against deployed URL:

```bash
cd /Users/lee/Downloads/VScode/oura_navi_monitor
MONITOR_E2E_BASE_URL="https://oura-navi-monitor-643644246736.us-central1.run.app" \
MONITOR_E2E_ADMIN_EMAIL="2401145@tc.terumo.co.jp" \
./scripts/run_e2e_chart_stability.sh
```

Files:

- `e2e/playwright.config.js`
- `e2e/tests/chart-stability.spec.js`
- `scripts/run_e2e_chart_stability.sh`
