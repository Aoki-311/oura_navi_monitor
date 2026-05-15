# 監視フロントエンド実装計画

## 1. 目的

本ドキュメントは、OurA Navi Monitor 次期フロントエンドを実装するための実行計画です。

既存文書の役割は以下の通りです。

| 文書 | 役割 |
| --- | --- |
| `MONITOR_FRONTEND_INFORMATION_ARCHITECTURE.md` | 画面で何を見せるか、表示名、指標説明、画面構成を定義する。 |
| `MONITOR_API_CONTRACT.md` | フロントエンドが取得できる API payload を定義する。 |
| `MONITOR_METRIC_CONTRACT.md` | 指標計算式と指標ステータスを定義する。 |
| `MONITOR_DATA_ARCHITECTURE.md` | BigQuery / Firestore / aggregate のデータ基盤を定義する。 |
| 本文書 | API payload をフロントエンドの状態、画面、コンポーネントへどう変換するかを定義する。 |

次期フロントエンドでは、API response を画面に直接流し込みません。必ず `API client -> adapter -> view model -> component` の順に変換します。

## 2. 基本方針

### 2.1 新フロントエンドの接続先

MVP の新フロントエンドは、第一階層を `ダッシュボード` に集約します。`回答品質`、`追問分析`、`ユーザー監視一覧` は独立ページではなく、ダッシュボード内セクションとして実装します。深いユーザー管理、会話確認、メッセージ確認、ユーザー単位の品質分析は `ユーザー監視一覧` の `詳細` から `ユーザー詳細` へドリルダウンして表示します。

| フロントエンド表示領域 | API | 扱い |
| --- | --- | --- |
| `ダッシュボード` | `GET /api/metrics/system-dashboard` | KPI、利用推移、活性度、環境・モード、回答品質、追問を表示する。 |
| `ユーザー監視一覧` | `GET /api/metrics/users` | ダッシュボード下部のユーザー一覧として表示する。 |
| `ユーザー詳細` | `GET /api/metrics/users/{user_id}` | ユーザー一覧から `詳細` で遷移する。 |
| `会話・メッセージ確認` | `GET /api/trace/messages` | ユーザー詳細内で会話選択後に lazy load する。独立ページにはしない。 |
| `エクスポート` | `POST /api/export/jobs`, `GET /api/export/jobs/{job_id}/download` | ダッシュボード右上またはユーザー詳細右上から起動する。UI 導線は MVP 必須。 |

`GET /api/metrics/dashboard` は旧UI互換用です。新UIからは使用しません。

`GET /api/metrics/answer-quality`、`GET /api/metrics/followup`、`GET /api/metrics/schema-health` は backend/API 能力として維持しますが、MVP の第一階層 UI では直接ページ化しません。必要になった場合のみ、専門運用者向け advanced console として後続追加します。

### 2.2 画面側の責務

| 層 | 責務 |
| --- | --- |
| API client | fetch、timeout、query parameter、HTTP error を扱う。 |
| Adapter | API payload を画面用 view model に変換する。 |
| View model | 表示名、単位、比率、空値、タグ、chart dataset を持つ。 |
| Component | view model を描画する。API field 名を直接参照しない。 |
| Page state | 期間、フィルター、検索条件、ページング、loading、empty、error を管理する。 |

### 2.3 禁止事項

| 禁止事項 | 理由 |
| --- | --- |
| component 内で API field を直接読む | payload 変更時に画面全体が壊れるため。 |
| chart component 内で率や件数を再計算する | 指標口径が分散するため。 |
| `null` をそのまま `0` と表示する | データなしとゼロを混同するため。 |
| message 本文を初期表示で取得する | PII / 業務情報リスクが高いため。 |
| 旧 `/api/metrics/dashboard` を新画面で使う | 旧UI互換 payload であり、新指標契約と一致しないため。 |

## 3. フロントエンド適配ロジック

### 3.1 推奨ファイル構成

現行フロントエンドは `frontend/app.js` の単一ファイル構成です。次期実装では、React へ移行しない場合でも、少なくとも以下の責務分割を行います。

```text
frontend/
  api/
    client.js
    metricsApi.js
    traceApi.js
    exportApi.js
  adapters/
    dashboardAdapter.js
    usersAdapter.js
    userDetailAdapter.js
    traceMessagesAdapter.js
    exportAdapter.js
  viewModels/
    labels.js
    formatters.js
    metricStatus.js
    timeRange.js
  pages/
    dashboardPage.js
    userDetailPage.js
  components/
    kpiCard.js
    chartCard.js
    dataTable.js
    exportDialog.js
    emptyState.js
    errorState.js
```

MVP では existing static HTML / vanilla JS を維持しても構いません。ただし `adapter` と `view model` の分離は必須です。

### 3.2 共通 API client

全 API request は共通 client を通します。

```javascript
async function getJson(path, params, options = {}) {
  const url = new URL(path, window.location.origin);
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value) !== "") {
      url.searchParams.set(key, String(value));
    }
  });

  const controller = new AbortController();
  const timeoutMs = options.timeoutMs || 18000;
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(url.toString(), {
      credentials: "same-origin",
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    return await response.json();
  } finally {
    window.clearTimeout(timer);
  }
}
```

### 3.3 共通期間 query

すべてのページは同じ期間指定 view model を使います。

```javascript
function toTimeRangeQuery(state) {
  if (state.preset === "custom") {
    return {
      preset: "custom",
      start: state.start,
      end: state.end,
    };
  }
  return {
    preset: state.preset || "today",
  };
}
```

表示名は以下で固定します。

| UI表示 | API値 |
| --- | --- |
| `今日` | `today` |
| `直近6時間` | `last_6h` |
| `直近12時間` | `last_12h` |
| `過去3日` | `last_3d` |
| `過去7日` | `last_7d` |
| `過去14日` | `last_14d` |
| `過去30日` | `last_30d` |
| `カスタム` | `custom` |

### 3.4 共通フォーマット

```javascript
function numberOrNull(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function countOrZero(value) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? Math.trunc(n) : 0;
}

function displayCount(value) {
  return countOrZero(value).toLocaleString("ja-JP");
}

function displayRate(value) {
  const n = numberOrNull(value);
  return n === null ? "-" : `${(n * 100).toFixed(1)}%`;
}

function displayMs(value) {
  const n = numberOrNull(value);
  return n === null ? "-" : `${Math.round(n).toLocaleString("ja-JP")} ms`;
}

function displayNullable(value) {
  if (value === undefined || value === null || value === "") return "-";
  return String(value);
}
```

`null` は `-` と表示します。件数のみ、データ欠落時に `0` と扱います。

### 3.5 ラベル変換

API が label 済みの値を返す場合でも、フロントエンド側で raw value fallback を持ちます。

```javascript
const MODE_LABELS = {
  internal: "社内モード",
  websearch: "Web検索モード",
  standard: "標準モード",
  deepthinking: "深考モード",
};

const DEVICE_LABELS = {
  desktop: "PC",
  mobile: "モバイル",
  unknown: "不明",
};

const ROLE_LABELS = {
  user: "ユーザー",
  assistant: "AI回答",
  system: "システム",
};

const STATUS_LABELS = {
  done: "完了",
  error: "エラー",
  aborted: "取消し",
  streaming: "生成中",
};

function labelOf(labels, raw, fallback = "不明") {
  const key = String(raw || "").trim().toLowerCase();
  return labels[key] || String(raw || "").trim() || fallback;
}
```

### 3.6 指標ステータス変換

`meta.metricStatus.answerSuccessRate` は UI バッジへ変換します。

| API値 | UI表示 | 意味 |
| --- | --- | --- |
| `official` | `正式値` | action event 連携後の正式口径です。 |
| `proxy` | `暫定値` | 過去データなど、正式 event が不足するため代替口径です。 |
| `mixed` | `正式値・暫定値混在` | 選択期間内に正式口径と暫定口径が混在しています。 |
| `unknown` | `データなし` | 対象期間に回答データがありません。 |

```javascript
function toMetricStatusBadge(status) {
  const normalized = String(status || "unknown").toLowerCase();
  const map = {
    official: { label: "正式値", tone: "success" },
    proxy: { label: "暫定値", tone: "warning" },
    mixed: { label: "正式値・暫定値混在", tone: "warning" },
    unknown: { label: "データなし", tone: "muted" },
  };
  return map[normalized] || map.unknown;
}
```

### 3.7 Loading / empty / error / partial

画面状態は必ず以下に分けます。

| 状態 | 表示 |
| --- | --- |
| `loading` | skeleton または loading shimmer。前回データがあれば薄く残す。 |
| `empty` | `対象期間に表示できるデータがありません。` |
| `error` | `データ取得に失敗しました。時間をおいて再試行してください。` |
| `partial` | `一部データの取得に失敗しました。表示可能な項目のみ表示しています。` |
| `stale` | `最新反映まで遅延している可能性があります。` |

`system-dashboard` は snapshot を使うため、`meta.dataDelaySec` または `meta.generatedAt` を確認し、必要なら `stale` 表示を出します。

## 4. Page ViewModel 定義

### 4.1 DashboardViewModel

接続 API:

```text
GET /api/metrics/system-dashboard
```

ダッシュボードの表示順は固定します。

```text
KPIサマリー
利用推移
活性度分布（14日）
利用環境・モード分析
回答品質分析
追問分析
ユーザー監視一覧
```

ViewModel:

```javascript
{
  windowLabel: "今日",
  metricStatus: {
    answerSuccessRate: { label: "正式値", tone: "success" }
  },
  kpis: [
    {
      key: "activeUserCount",
      label: "アクティブユーザー数",
      value: "36",
      help: "選択した期間内に実際にチャットを利用したユーザー数です。"
    },
    {
      key: "answerSuccessRate",
      label: "回答成功率",
      value: "94.0%",
      statusBadge: { label: "正式値", tone: "success" },
      help: "エラー表示がなく、回答再生成・回答強化・修正要求・低評価が確認されていない回答の割合です。"
    },
    {
      key: "lowCoverageRate",
      label: "低カバレッジ率",
      value: "8.0%",
      help: "根拠資料や引用が不足している可能性がある回答の割合です。"
    },
    {
      key: "errorRate",
      label: "エラー率",
      value: "1.0%",
      help: "回答生成や通信処理でエラーになった割合です。"
    },
    {
      key: "p95LatencyMs",
      label: "P95応答時間",
      value: "2,400 ms",
      help: "利用者の大半が待つ最大に近い応答時間の目安です。"
    }
  ],
  usageTrendChart: {
    labels: ["2026-05-13"],
    bars: [{ label: "アクティブユーザー数", data: [36] }],
    lines: [{ label: "メッセージ数", data: [420] }]
  },
  activityDistributionChart: {
    centerLabel: "総ユーザー数",
    centerValue: "12,856",
    segments: []
  },
  requestByHourChart: {},
  deviceDistributionChart: {},
  modeDistributionChart: {},
  answerQualityCharts: {},
  followupChart: {}
}
```

Adapter:

```javascript
function toDashboardViewModel(payload) {
  const metricStatus = payload.meta?.metricStatus || {};
  const kpis = payload.kpis || {};
  const activity = payload.activityDistribution || {};
  const environment = payload.environmentMode || {};

  return {
    window: payload.window,
    metricStatus: {
      answerSuccessRate: toMetricStatusBadge(metricStatus.answerSuccessRate),
    },
    kpis: [
      buildKpi("activeUserCount", "アクティブユーザー数", displayCount(kpis.activeUserCount), KPI_HELP.activeUserCount),
      buildKpi("answerSuccessRate", "回答成功率", displayRate(kpis.answerSuccessRate), KPI_HELP.answerSuccessRate, toMetricStatusBadge(metricStatus.answerSuccessRate)),
      buildKpi("lowCoverageRate", "低カバレッジ率", displayRate(kpis.lowCoverageRate), KPI_HELP.lowCoverageRate),
      buildKpi("errorRate", "エラー率", displayRate(kpis.errorRate), KPI_HELP.errorRate),
      buildKpi("p95LatencyMs", "P95応答時間", displayMs(kpis.p95LatencyMs), KPI_HELP.p95LatencyMs),
    ],
    usageTrendChart: toUsageTrendChart(payload.usageTrend || []),
    activityDistributionChart: toActivityDistributionChart(activity),
    requestByHourChart: toLineChart(environment.requestByHour || [], "hour", "requestCount", "リクエスト数"),
    deviceDistributionChart: toDoughnutChart(environment.deviceDistribution || []),
    modeDistributionChart: toDoughnutChart(environment.modeDistribution || []),
    answerQualityCharts: toAnswerQualityCharts(payload.answerQuality || {}),
    followupChart: toFollowupSummaryChart(payload.followup || {}),
  };
}
```

### 4.2 Dashboard AnswerQualitySectionViewModel

接続 API:

```text
GET /api/metrics/system-dashboard
```

`回答品質分析` は独立ページではなく、ダッシュボード内セクションとして表示します。

画面表示:

| セクション | 表示 |
| --- | --- |
| `回答品質サマリー` | 回答数、回答成功率、低カバレッジ率、平均カバレッジ、structured-led rate |
| `回答可能性` | 分布 chart |
| `回答利用可能性` | 分布 chart |
| `業務利用可能性` | 分布 chart |
| `根拠十分性` | 分布 chart |

Adapter では `payload.answerQuality.answerability` などを `label/count/rate` の chart rows に統一します。詳細な reason や raw governance payload は、MVP では BigQuery / export / ユーザー詳細の内部調査導線に保持します。

### 4.3 Dashboard FollowupSectionViewModel

接続 API:

```text
GET /api/metrics/system-dashboard
```

`追問分析` も独立ページではなく、ダッシュボード内セクションとして表示します。

画面表示:

| セクション | 表示 |
| --- | --- |
| `追問サマリー` | 追問認識数、追問成功率、明示的な訂正、確認が必要な追問 |
| `追問ファネル` | `追問認識 -> 追問成功` |
| `理由内訳` | `失敗理由`, `状態処理`, `判定根拠`, `文脈外追問` |

Adapter:

```javascript
function toFollowupViewModel(payload) {
  const summary = payload.followup || {};
  return {
    summaryCards: [
      buildKpi("recognizedCount", "追問認識数", displayCount(summary.recognizedCount)),
      buildKpi("successRate", "追問成功率", displayRate(summary.successRate)),
      buildKpi("explicitCorrectionCount", "明示的な訂正", displayCount(summary.explicitCorrectionCount)),
      buildKpi("clarificationRequiredCount", "確認が必要な追問", displayCount(summary.clarificationRequiredCount)),
    ],
    funnelChart: toBarChart([
      { label: "追問認識", count: summary.recognizedCount },
      { label: "追問成功", count: summary.successCount },
      { label: "明示的な訂正", count: summary.explicitCorrectionCount },
      { label: "確認が必要な追問", count: summary.clarificationRequiredCount },
    ]),
    reasonTable: [],
  };
}
```

### 4.4 UserListViewModel

接続 API:

```text
GET /api/metrics/users
```

Query:

```javascript
{
  ...toTimeRangeQuery(timeRange),
  activity: filters.activity,
  q: filters.query,
  limit: 100,
  cursor: page.cursor
}
```

Table columns:

| UI列 | ViewModel key | API field |
| --- | --- | --- |
| `ユーザーID` | `userId` | `userId` |
| `メールアドレス` | `userEmail` | `userEmail` |
| `最終利用日時` | `lastActiveAtJst` | `lastActiveAtJst` |
| `直近7日利用日数` | `activeDays7` | `activeDays7` |
| `直近7日メッセージ数` | `messageCount7d` | `messageCount7d` |
| `根拠カバレッジ率` | `coverageRate` | `coverageRate` |
| `低評価率` | `badFeedbackRate` | `badFeedbackRate` |
| `活性度区分` | `activityLevel` | `activityLevel` |
| `詳細` | `detailAction` | derived |

`coverageRate` は API が返す場合はその値を使います。API が `lowCoverageRate` しか返さない場合だけ `1 - lowCoverageRate` を adapter で計算します。

### 4.5 UserDetailViewModel

接続 API:

```text
GET /api/metrics/users/{user_id}
```

重要ルール:

| ルール | 理由 |
| --- | --- |
| `include_messages=false` 固定 | ユーザー詳細の初期表示を軽量化するため。 |
| 会話一覧は `conversation_limit=50` | 初期表示を安定させるため。 |
| message 明細は `/api/trace/messages` で遅延取得 | 本文・PII・重い Firestore scan を分離するため。 |

ViewModel:

```javascript
{
  userSummaryCards: [],
  trendChart: {},
  modeDistributionChart: {},
  answerQualityCharts: {},
  followupChart: {},
  conversationTable: [],
  messageLoading: {
    endpoint: "/api/trace/messages",
    includeMessagesInThisResponse: false
  },
  nextConversationCursor: ""
}
```

会話 row の `詳細` クリック時に呼ぶ API:

```text
GET /api/trace/messages?conversation_id={conversationId}&user_id={userId}&limit=100&include_content=false
```

### 4.6 UserDetail MessageChainViewModel

接続 API:

```text
GET /api/trace/messages
```

`/api/trace/messages` は独立した `チャット記録確認` ページではなく、`ユーザー詳細` 内の会話・メッセージ確認 drawer / lower panel で使用します。

検索条件:

```text
conversation_id
trace_id
turn_id
user_id
user_email
status
mode
preset / start / end
limit
cursor
include_content
```

初期状態では `include_content=false` とします。

本文表示ボタンを押した場合のみ、同じ検索条件で `include_content=true` を再取得します。本文表示ボタンには以下の確認文を出します。

```text
メッセージ本文には個人情報や業務情報が含まれる可能性があります。本文を表示しますか？
```

Message row view model:

| UI列 | ViewModel key | API field |
| --- | --- | --- |
| `送信日時（日本時間）` | `timestampJst` | `timestampJst` |
| `役割` | `roleLabel` | `roleLabel` / `roleRaw` |
| `状態` | `statusLabel` | `statusLabel` / `statusRaw` |
| `送信時モード` | `modeAtSendLabel` | `modeAtSendLabel` / `modeAtSendRaw` |
| `デバイス` | `deviceLabel` | `deviceLabel` / `deviceClassRaw` |
| `入力元` | `clientOrigin` | `clientOrigin` |
| `評価` | `feedback` | `feedback` |
| `本文` | `contentPreview` | `contentPreview` |
| `Trace ID` | `traceId` | `traceId` |
| `Request ID` | `requestId` | `requestId` |
| `Turn ID` | `turnId` | `turnId` |
| `Message ID` | `messageId` | `messageId` |

`payloadEvents` は初期 UI では折りたたみ表示とし、`イベント連携` または `Payload候補` として件数と event family のみ表示します。グローバルな trace_id / turn_id 横断検索 UI は MVP では作りません。

### 4.7 DataHealth handling

接続 API:

```text
GET /api/metrics/schema-health
```

`schema-health` は backend/API 能力として維持しますが、MVP の独立フロントエンドページにはしません。必要な場合は Cloud Run logs、BigQuery、export、または制限付き内部調査導線で確認します。

フロントエンドに表示する場合でも、通常画面では以下のような最小ステータスに限定します。

| セクション | 表示 |
| --- | --- |
| `イベント件数` | event family / schema version 別件数 |
| `必須項目欠落` | trace, request, conversation, turn, message, mode の欠落数 |
| `Schema不一致` | mismatch / unknown / deprecated |
| `Join健全性` | join rate, unjoined count |
| `データ遅延` | generatedAt, dataDelaySec |

## 5. エクスポート adapter

### 5.1 ExportDialogViewModel

全画面の `エクスポート` ボタンは同じ dialog component を使います。

```javascript
{
  scope: "system" | "user" | "trace",
  title: "エクスポート設定",
  defaultPreset: currentTimeRange.preset,
  outputDataOptions: [],
  fieldGroupOptions: [],
  personalInfoModeOptions: [
    { value: "masked", label: "匿名化して出力" },
    { value: "full", label: "管理者権限で原文を含める" }
  ],
  includeContentDefault: false
}
```

### 5.2 Export job request

```javascript
function toExportJobRequest(dialogState, pageFilters) {
  return {
    preset: dialogState.preset,
    start: dialogState.start,
    end: dialogState.end,
    outputData: dialogState.outputData,
    includedFields: dialogState.includedFields,
    personalInfoMode: dialogState.personalInfoMode,
    includeContent: dialogState.includedFields.includes("メッセージ本文"),
    filters: pageFilters,
  };
}
```

`メッセージ本文` は初期選択しません。選択時は確認 dialog を出します。

## 6. 画面別実装順序

### Phase 0: 旧UI互換の維持

目的は、次期UI移行中に現行画面を壊さないことです。

| Task | 内容 | 完了条件 |
| --- | --- | --- |
| F0-1 | 旧 `/api/metrics/dashboard` を旧UI互換として維持 | 現行画面の数字が表示される。 |
| F0-2 | 新UIでは旧 endpoint を使わない方針を明文化 | adapter で `/api/metrics/system-dashboard` を参照する。 |
| F0-3 | 旧UI互換 endpoint に `meta.deprecated=true` を保持 | 移行完了後に廃止判断できる。 |

### Phase 1: Frontend foundation

| Task | 内容 | 完了条件 |
| --- | --- | --- |
| F1-1 | `api/client.js` を追加 | timeout、HTTP error、query parameter が共通化される。 |
| F1-2 | `viewModels/formatters.js` を追加 | count、rate、ms、null 表示が統一される。 |
| F1-3 | `viewModels/labels.js` を追加 | mode、device、role、status の日本語表示が統一される。 |
| F1-4 | `viewModels/metricStatus.js` を追加 | `official/proxy/mixed/unknown` の badge 表示が統一される。 |
| F1-5 | 共通 loading / empty / error component を追加 | 画面ごとに例外処理が分散しない。 |

### Phase 2: Dashboard

| Task | 内容 | API | 完了条件 |
| --- | --- | --- | --- |
| F2-1 | `dashboardAdapter.js` を追加 | `/api/metrics/system-dashboard` | DashboardViewModel が生成できる。 |
| F2-2 | KPI 5カードを実装 | 同上 | `?` 定義説明、metric status badge が表示される。 |
| F2-3 | 利用推移を実装 | 同上 | 縦棒=アクティブユーザー、折れ線=メッセージ数。 |
| F2-4 | 活性度分布を実装 | 同上 | 円グラフ中央に総ユーザー数。 |
| F2-5 | 利用環境・モード分析を実装 | 同上 | 時間帯別、デバイス、モードの3グラフ。 |
| F2-6 | 回答品質・追問サマリーを実装 | 同上 | 4品質分布と追問指標が表示される。 |
| F2-7 | `usersAdapter.js` を追加 | `/api/metrics/users` | Dashboard 下部の UserListViewModel が生成できる。 |
| F2-8 | ユーザー監視一覧を実装 | 同上 | 指定列、活性度 filter、検索、`詳細` が動く。 |

### Phase 3: User detail

| Task | 内容 | API | 完了条件 |
| --- | --- | --- | --- |
| F3-1 | `userDetailAdapter.js` を追加 | `/api/metrics/users/{user_id}` | UserDetailViewModel が生成できる。 |
| F3-2 | ユーザーサマリーを実装 | 同上 | summary cards が表示される。 |
| F3-3 | 推移・モード・品質・追問を実装 | 同上 | 各 chart が空値に強く表示される。 |
| F3-4 | 会話一覧を実装 | 同上 | `conversation_limit=50` でページングできる。 |
| F3-5 | 会話 row から message lazy load | `/api/trace/messages` | drawer / lower panel でメッセージ一覧を取得する。 |
| F3-6 | 単一ユーザー export dialog を実装 | `/api/export/jobs` | 選択ユーザーのデータを出力できる。 |

### Phase 4: Message chain in user detail

| Task | 内容 | API | 完了条件 |
| --- | --- | --- | --- |
| F4-1 | `traceMessagesAdapter.js` を追加 | `/api/trace/messages` | MessageChainViewModel が生成できる。 |
| F4-2 | conversation 選択連動を実装 | 同上 | ユーザー詳細の会話選択から message preview を表示できる。 |
| F4-3 | preview-only message list を実装 | 同上 | 初期表示では `contentPreview` のみ表示する。 |
| F4-4 | 本文表示 confirm を実装 | 同上 | 明示操作時のみ `include_content=true` を送る。 |
| F4-5 | payload events 折りたたみ表示 | 同上 | event family と join key を確認できる。 |

### Phase 5: Export dialog

| Task | 内容 | API | 完了条件 |
| --- | --- | --- | --- |
| F5-1 | 共通 `ExportDialog` UI を実装 | UI only | 期間、出力データ、出力項目、個人情報を選択できる。 |
| F5-2 | Dashboard export UI を配置 | UI only | ダッシュボード右上からユーザー監視一覧とメッセージ明細を選べる。 |
| F5-3 | User detail export UI を配置 | UI only | ユーザー詳細右上から選択ユーザーの summary、会話、メッセージを選べる。 |
| F5-4 | export/jobs 実処理を接続 | `/api/export/jobs` | Dashboard / User detail の dialog から実ファイルを生成できる。 |

Export は画面上の必須導線です。ただし開発順序としては、dashboard と user detail の表示が安定した後に `export/jobs` の実処理を接続します。UI 導線自体は後回しにしません。

### Phase 6: Backend-only diagnostics

| Task | 内容 | 完了条件 |
| --- | --- | --- |
| F6-1 | `answer-quality`, `followup`, `schema-health` をフロントエンドページ化しない | MVP UI に独立ナビを出さない。 |
| F6-2 | 必要な詳細は export / BigQuery に保持 | 技術調査導線は失わない。 |

### Phase 7: Legacy retirement

| Task | 内容 | 完了条件 |
| --- | --- | --- |
| F7-1 | 新UIが新APIへ移行 | Network log で `/api/metrics/dashboard` が呼ばれない。 |
| F7-2 | 旧 endpoint 利用ログを確認 | 7日以上、旧 endpoint への新UIアクセスがない。 |
| F7-3 | `/api/metrics/dashboard` を 410 または完全 alias に変更 | 現行UIの依存がなくなってから実施する。 |

## 7. UI/UX 詳細ルール

### 7.1 画面階層

MVP の第一階層:

```text
ダッシュボード
```

`回答品質分析`、`追問分析`、`ユーザー監視一覧` はダッシュボード内セクションです。`ユーザー詳細` は nav に置かず、`ユーザー監視一覧` の `詳細` から遷移します。`チャット記録確認` と `データ健全性` は独立フロントエンドページにしません。

MVP では左側ナビゲーションを採用しません。画面上部にタイトル、期間選択、更新、`エクスポート` を配置し、必要に応じてダッシュボード内セクション anchor とユーザー詳細の breadcrumb のみを使用します。

### 7.2 Visual direction

デザイン目標は `Enterprise Operations Console` とします。目的は「安定、可信、可审计、低噪音、便于定位问题」です。参考画像のような dark dashboard の雰囲気は参考にできますが、強い neon / glow / cyber 方向には寄せません。

推奨方針:

| 項目 | 方針 |
| --- | --- |
| 基本テーマ | 浅色または半浅色を既定にする。長時間の表格・本文確認を優先する。 |
| 背景 | `#F6F8FB` 付近の淡いグレー。 |
| カード | `#FFFFFF`、薄い境界線、明確な余白。 |
| ヘッダー領域 | 深蓝または graphite をアクセントに使う。 |
| 強調色 | 青、青緑、橙、赤を semantic status のみに使う。 |
| 動き | loading / state change の最小限に限定する。 |

Dark theme を後続で追加する場合も `Dark Enterprise Console` に留め、発光表現を抑えます。

### 7.3 KPI help

各 KPI は `?` ボタンを持ちます。

| KPI | Help text |
| --- | --- |
| `アクティブユーザー数` | 選択した期間内に実際にチャットを利用したユーザー数です。 |
| `回答成功率` | エラー表示がなく、回答再生成・回答強化・修正要求・低評価が確認されていない回答の割合です。 |
| `低カバレッジ率` | 根拠資料や引用が不足している可能性がある回答の割合です。 |
| `エラー率` | 回答生成や通信処理でエラーになった割合です。 |
| `P95応答時間` | 利用者の大半が待つ最大に近い応答時間の目安です。 |

### 7.4 色と状態

| 状態 | 色の意味 |
| --- | --- |
| 正常 | 青緑または緑系 |
| 注意 | 黄または橙系 |
| 危険 | 赤系 |
| 情報 | 青系 |
| 無効 / データなし | グレー |

色だけで意味を伝えず、必ず label / icon / tooltip を併用します。

### 7.5 テーブル

| UI | ルール |
| --- | --- |
| ユーザー一覧 | sticky header、列 sort、検索、活性度 filter、ページング。 |
| 会話一覧 | title と preview は長文省略。ID は copy button を付ける。 |
| メッセージ一覧 | 本文は preview 基本。trace/request/turn/message ID は横スクロールまたは折りたたみ。 |
| payload events | 初期は折りたたみ。技術者調査時だけ展開。 |

### 7.6 モバイル対応

管理者利用は PC 前提ですが、モバイルでも最低限の閲覧は可能にします。

| 幅 | 挙動 |
| --- | --- |
| `>= 1200px` | 3カラム chart、広い table。 |
| `768px - 1199px` | 2カラム chart、table 横スクロール。 |
| `< 768px` | 1カラム、filter は drawer または accordion。 |

## 8. テスト計画

### 8.1 Adapter unit test

| Test | 内容 |
| --- | --- |
| Dashboard adapter | `system-dashboard` payload から KPI / chart view model を生成できる。 |
| Metric status | `official/proxy/mixed/unknown` が正しい badge になる。 |
| Null handling | `null` は `-`、件数は `0` になる。 |
| User adapter | `coverageRate` fallback が `1 - lowCoverageRate` になる。 |
| Message chain adapter | `includeContent=false` で `content` を表示しない。 |

### 8.2 API contract fixture test

`docs/MONITOR_API_CONTRACT.md` の sample payload に近い fixture を保存し、adapter が壊れないことを確認します。

```text
frontend/tests/fixtures/systemDashboard.json
frontend/tests/fixtures/users.json
frontend/tests/fixtures/userDetail.json
frontend/tests/fixtures/traceMessagesPreview.json
frontend/tests/fixtures/traceMessagesWithContent.json
```

### 8.3 UI smoke

| Smoke | 確認 |
| --- | --- |
| Dashboard | 5 KPI と主要 chart が表示される。 |
| User monitoring section | ダッシュボード下部の一覧、filter、詳細遷移が動く。 |
| User detail | message を含まず初期表示される。 |
| Message chain | ユーザー詳細内で preview-only と本文表示 confirm が動く。 |
| Export dialog | 初期選択と custom range が動く。 |

### 8.4 Live smoke

Cloud Run デプロイ後に browser network log で確認します。

| 確認項目 | 期待 |
| --- | --- |
| `/api/metrics/system-dashboard?preset=today` | 200、`kpis` が存在する。 |
| `/api/metrics/users?preset=last_7d` | 200、`users` が存在する。 |
| `/api/metrics/users/{user_id}?include_messages=false` | 200、message 明細を含まない。 |
| `/api/trace/messages?conversation_id=...&user_id=...&include_content=false` | 200、ユーザー詳細からの lazy load で `contentPreview` のみ。 |
| `/api/trace/messages?conversation_id=...&user_id=...&include_content=true` | 200、明示操作時のみ `content` を含む。 |
| 新UI network | `/api/metrics/dashboard` を呼ばない。 |

## 9. 実装上の注意

### 9.1 旧UIと新UIの共存

移行期間中は以下を守ります。

| 項目 | 方針 |
| --- | --- |
| 旧UI | `/api/metrics/dashboard` で互換維持。 |
| 新UI | `/api/metrics/system-dashboard` など新APIのみ使用。 |
| endpoint 廃止 | 新UI移行後、旧UIアクセスがないことを確認してから実施。 |

### 9.2 Backend payload の揺れに対する耐性

Adapter は以下を許容します。

| 揺れ | 対応 |
| --- | --- |
| `null` | `-` 表示。 |
| 配列なし | 空配列に変換。 |
| label 済み / raw 混在 | label 済みを優先し、raw fallback を使う。 |
| metric status なし | `unknown` とする。 |
| `coverageRate` なし | `1 - lowCoverageRate` で fallback。 |

### 9.3 セキュリティ

| 領域 | 方針 |
| --- | --- |
| 本文表示 | 初期は preview-only。明示操作時のみ全文取得。 |
| export | `メッセージ本文` は初期未選択。 |
| 技術ID | 表示・copy 可能。ただし通常 table では折りたたみも許容。 |
| raw payload | MVP 画面では表示しない。BigQuery / export / restricted debug で保持。 |

## 10. 完了条件

次期フロントエンドの MVP 完了条件は以下です。

| 領域 | 完了条件 |
| --- | --- |
| API接続 | 新UIが `/api/metrics/dashboard` を呼ばない。 |
| Dashboard | 5 KPI、利用推移、活性度、環境・モード、品質、追問が表示される。 |
| User monitoring section | ダッシュボード下部で一覧、活性度 filter、検索、詳細遷移、export dialog が動く。 |
| User detail | 軽量 summary と会話一覧が表示され、message は lazy load。 |
| Message chain | ユーザー詳細内で conversation 選択、preview-only、本文明示表示が動く。 |
| Backend-only diagnostics | `チャット記録確認` と `データ健全性` を独立ページとして表示しない。 |
| 状態管理 | loading / empty / error / partial / stale が実装される。 |
| 日本語表示 | 管理者向けのビジネス日本語表示名に統一される。 |
| テスト | adapter unit、fixture、UI smoke、live smoke が通る。 |
