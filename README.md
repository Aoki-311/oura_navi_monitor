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

## 9. Suggested Operations Policy

- Keep monitor service isolated and read-only against production data sources.
- Keep SA key out of runtime; rotate and delete temporary key after bootstrap.
- Restrict access to allowlisted admins only.
- Audit monitor access and message drilldown usage in Cloud Logging.
