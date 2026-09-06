const freshness = {
  state: "fresh",
  dataThrough: "2026-08-23T01:00:00Z",
};
const scopeMetadata = (scope) => ({
  scope,
  scopePolicyVersion: "summary_role_v1",
  rosterFingerprint: "roster-fingerprint-1",
  contentFingerprint: "content-fingerprint-1",
  publishedRunId: "run-20260823-01",
  windowStart: "2026-08-16T15:00:00Z",
  windowEnd: "2026-08-23T01:00:00Z",
  windowTimezone: "Asia/Tokyo",
});
const measurementState = (measuredCount, totalCount) => {
  if (totalCount === 0) return "no_usage";
  if (measuredCount === 0) return "not_measured";
  if (measuredCount < totalCount) return "partial";
  return "measured";
};
const measurementReason = (measuredCount, totalCount) => totalCount === 0
  ? "no_usage"
  : measuredCount === totalCount
    ? "complete"
    : "historical_unavailable";
const measurement = (value, measuredCount, totalCount, reason = measurementReason(measuredCount, totalCount)) => ({
  value, measuredCount, totalCount,
  measurementState: measurementState(measuredCount, totalCount),
  measurementReason: reason,
});
const completeContentDiagnostics = {
  state: "complete",
  labelCatalogStatus: "available",
  rosterStatus: "available",
  rosterIsolatedCount: 0,
  rosterIssueCounts: {},
  issues: [],
};
const analyticsQuality = (measuredCount, totalCount) => {
  const axis = {
    measuredCount,
    totalCount,
    measurementState: measurementState(measuredCount, totalCount),
    measurementReason: measurementReason(measuredCount, totalCount),
    isolatedCount: totalCount - measuredCount,
  };
  return {
    contractVersion: "dashboard_events_v2",
    isolatedEventCount: totalCount - measuredCount,
    totalEventCount: totalCount,
    classification: { ...axis },
    task: { ...axis },
    product: { ...axis },
    sourcePipeline: {
      publishedRunId: "run-20260823-01",
      latestRunId: "run-20260823-01",
      latestRunStatus: "succeeded",
      latestRunErrorCode: "",
      latestRunFinishedAt: "2026-08-23T01:00:00Z",
      diagnosticsStatus: "available",
      diagnosticsErrorCode: "",
      state: "degraded",
      quarantinedEventCount: 2,
      deduplicatedDeliveryCount: 3,
      repairedDuplicateFactCount: 1,
      axisUnmeasuredFindingCount: 7,
      batchBlockingFailureCount: 0,
    },
  };
};

const overview = {
  ...scopeMetadata("global"),
  contentDiagnostics: completeContentDiagnostics,
  scopeUserCount: 69,
  freshness,
  analyticsQuality: analyticsQuality(70, 77),
  kpis: {
    activeUsers: 24,
    adoptionRate: 24 / 69,
    returnRate: .5,
    questionsPerActiveUser: 3.2,
    completeDelivery: measurement(.91, 70, 77),
    p95Latency: { valueMs: 72000, ...measurement(null, 75, 77) },
  },
  hourlyQuestions: Array.from({ length: 24 }, (_, hour) => ({ hour: `${String(hour).padStart(2, "0")}:00`, count: hour + 1 })),
  deviceDistribution: [{ key: "desktop", label: "PC", count: 50, rate: .8 }, { key: "mobile", label: "モバイル", count: 12, rate: .2 }],
  deviceMeasurement: measurement(null, 62, 77),
  modeDistribution: [{ key: "internal", label: "社内モード", count: 49, rate: .79 }, { key: "websearch", label: "Web検索モード", count: 13, rate: .21 }],
  modeMeasurement: measurement(null, 62, 77),
  usageTrend: [{ date: "2026-08-22", activeUsers: 18, questions: 49, isPartial: false }, { date: "2026-08-23", activeUsers: 20, questions: 62, isPartial: true }],
  requestTasks: [{ key: "fact_lookup", label: "情報確認", count: 32, rate: .52 }, { key: "comparison_selection", label: "比較・選定", count: 20, rate: .32 }, { key: "unclassified", label: "判定不能", count: 10, rate: .16 }],
  taskMeasurement: measurement(null, 62, 77),
  activityDistribution: [{ key: "high", label: "高アクティブ", count: 10, rate: 10 / 69 }, { key: "middle", label: "中アクティブ", count: 14, rate: 14 / 69 }, { key: "low", label: "低アクティブ", count: 20, rate: 20 / 69 }, { key: "dormant", label: "休眠ユーザー", count: 25, rate: 25 / 69 }],
  activityByArea: [{ label: "関西", total: 10, segments: [{ key: "high", label: "高アクティブ", count: 3, rate: .3 }, { key: "middle", label: "中アクティブ", count: 2, rate: .2 }, { key: "low", label: "低アクティブ", count: 2, rate: .2 }, { key: "dormant", label: "休眠ユーザー", count: 3, rate: .3 }] }],
  activityByRole: [{ label: "本社MR", total: 10, segments: [{ key: "high", label: "高アクティブ", count: 3, rate: .3 }, { key: "middle", label: "中アクティブ", count: 2, rate: .2 }, { key: "low", label: "低アクティブ", count: 2, rate: .2 }, { key: "dormant", label: "休眠ユーザー", count: 3, rate: .3 }] }],
  topProducts: [{ label: "テルフュージョン", count: 28 }, { label: "ケモセーフ", count: 17 }],
  productTaskMatrix: [{ product: "テルフュージョン", task: "fact_lookup", taskLabel: "情報確認", count: 16 }, { product: "テルフュージョン", task: "comparison_selection", taskLabel: "比較・選定", count: 12 }],
  productResolution: { candidateCount: 45, resolvedCount: 45, unresolvedQuestions: 0, resolutionRate: 1, ...measurement(null, 45, 77) },
};

const users = { ...scopeMetadata("user_map"), contentDiagnostics: completeContentDiagnostics, scopeUserCount: 80, freshness, users: [
  { rosterId: "roster_1", name: "山田 太郎", email: "user1@example.com", role: "本社MR", department: "DM専任", workplace: "大阪", area: "関西", areaKey: "関西", labels: [{ labelId: "label_1", name: "重点", color: "#23d28f" }], lastActiveAt: "2026-08-23T01:00:00Z", activeDays7: 4, userMessageCount7: 12, activeDaysInPeriod: 4, userMessageCountInPeriod: 12, completeDelivery: measurement(.92, 11, 12), activity: "high", activityLabel: "高アクティブ" },
  { rosterId: "roster_2", name: "佐藤 花子", email: "user2@example.com", role: "コントラクトMR", department: "DM本社", workplace: "虎ノ門", area: "本社", areaKey: "本社・虎ノ門", labels: [], lastActiveAt: "", activeDays7: 0, userMessageCount7: 0, activeDaysInPeriod: 0, userMessageCountInPeriod: 0, completeDelivery: measurement(null, 0, 0), activity: "dormant", activityLabel: "休眠ユーザー" },
] };
const overviewUsers = { ...users, ...scopeMetadata("global"), scopeUserCount: 69 };

const regions = { ...scopeMetadata("global"), contentDiagnostics: completeContentDiagnostics, scopeUserCount: 69, freshness, regions: [
  { areaKey: "関西", area: "関西", rosterUsers: 10, activeUsers: 6, questions: 42, adoptionRate: .6, returnRate: .5 },
  { areaKey: "本社・虎ノ門", area: "本社・虎ノ門", rosterUsers: 19, activeUsers: 8, questions: 35, adoptionRate: 8 / 19, returnRate: .375 },
] };

const detail = {
  ...scopeMetadata("user_map"),
  contentDiagnostics: completeContentDiagnostics,
  freshness,
  analyticsQuality: analyticsQuality(18, 20),
  profile: { rosterId: "roster_1", name: "山田 太郎", email: "user1@example.com", area: "関西", workplace: "大阪", role: "本社MR", department: "DM専任", mrExperience: "10年", labels: users.users[0].labels },
  summary: { lastActiveAt: "2026-08-23T01:00:00Z", activeDays: 5, questions: 20, questionsPerActiveDay: 4, completeDelivery: measurement(.9, 18, 20), p95Latency: { valueMs: 72000, ...measurement(null, 18, 20) } },
  comparisons: {
    area: { label: "関西", peerCount: 10, averageQuestions: 8.2, averageActiveDays: 3.1, averageCompleteDelivery: measurement(.84, 8, 10) },
    role: { label: "本社MR", peerCount: 39, averageQuestions: 7.3, averageActiveDays: 2.8, averageCompleteDelivery: measurement(.86, 30, 39) },
  },
  trend: [{ date: "2026-08-22", questions: 7, completeDelivery: measurement(.86, 7, 7), isPartial: false }, { date: "2026-08-23", questions: 13, completeDelivery: measurement(.92, 11, 13), isPartial: true }],
  products: [{ label: "テルフュージョン", count: 12 }],
  tasks: [{ key: "fact_lookup", label: "情報確認", count: 4, rate: .2 }],
  productResolution: { candidateCount: 12, resolvedCount: 12, unresolvedQuestions: 0, resolutionRate: 1, ...measurement(null, 12, 20) },
  questionCategories: [{ key: "product_information", label: "製品情報・仕様", count: 12, rate: .6 }],
  questionCategoryMeasurement: measurement(null, 12, 20),
  taskMeasurement: measurement(null, 4, 20),
  modes: [{ key: "internal", label: "社内モード", count: 18, rate: .9 }],
  modeMeasurement: measurement(null, 18, 20),
  devices: [{ key: "desktop", label: "PC", count: 16, rate: .8 }],
  deviceMeasurement: measurement(null, 16, 20),
};

const newsUsage = {
  contractVersion: "news_usage_dashboard_v1", scope: "global", rosterId: "",
  windowStart: "2026-08-16T15:00:00Z", windowEnd: "2026-08-23T01:00:00Z", windowTimezone: "Asia/Tokyo",
  publishedRunId: "news_pub_1", rosterFingerprint: "roster_fixture", contentFingerprint: "news_fixture", scopePolicyVersion: "summary_role_v1",
  state: { availability: "available", freshness: "fresh" },
  totals: { tabViews: 10, newsTabViews: 6, societyTabViews: 4, contentClicks: 14, newsContentClicks: 9, societyContentClicks: 5, newsDomesticClicks: 6, newsOverseasClicks: 3, newsUnknownGeographyClicks: 0 },
  trend: [{ date: "2026-08-23", tabViews: 10, newsTabViews: 6, societyTabViews: 4, contentClicks: 14, newsContentClicks: 9, societyContentClicks: 5 }],
  newsCategories: [{ key: "regulatory_safety", label: "規制・安全", clicks: 9, domesticClicks: 6, overseasClicks: 3, unknownGeographyClicks: 0 }],
  societyCategories: [{ key: "糖尿病関連", label: "糖尿病関連", clicks: 5, sources: [{ key: "jds", label: "日本糖尿病学会", clicks: 5 }] }],
};

const conversations = { status: "ready", conversations: [{ conversationId: "conv_1", title: "製品情報の確認", messageCount: 4, updatedAt: "2026-08-23T01:00:00Z", updatedAtJst: "2026-08-23 10:00:00" }] };
const managedUsers = { users: [{ rosterId: "roster_1", name: "山田 太郎", email: "user1@example.com", area: "関西", areaKey: "関西", workplace: "大阪", role: "本社MR", department: "DM専任", mrExperience: "10年", labelIds: ["label_1"], isActive: true, identityBound: true, globalScopeEnabled: true, userMapScopeEnabled: true, scopePolicyVersion: "summary_role_v1", rosterIssues: [], updatedAt: "2026-08-23T01:00:00Z", updatedBy: "admin@example.com" }] };
const managedLabels = { labels: [{ labelId: "label_1", name: "重点", color: "#23d28f", usageCount: 1, isActive: true, labelIssues: [], updatedAt: "2026-08-23T01:00:00Z", updatedBy: "admin@example.com" }] };
const managementMetadata = { areas: ["北海道東北", "関東A", "関東B", "首都圏A", "首都圏B", "東海北陸", "関西", "中四国", "九州", "本社"], workplaces: ["大阪", "虎ノ門"], roles: ["本社MR", "コントラクトMR", "本社メンバー"], summaryRoles: ["本社MR", "コントラクトMR"], departments: ["DM専任", "ヘルスケア本社", "DM本社", "管理者"], scopePolicyVersion: "summary_role_v1", labelColors: ["#23d28f", "#386dff", "#ffb340", "#ff5b74", "#7c5cff", "#27d9d2", "#5f6285"] };

const canonicalText = (value) => String(value ?? "").normalize("NFKC").trim().replace(/\s+/gu, " ");
const canonicalEmail = (value) => String(value ?? "").normalize("NFKC").trim().toLowerCase()
  .replaceAll("ß", "ss").replaceAll("ς", "σ");

function makeAnalyticsUsers(count = 80) {
  return Array.from({ length: count }, (_, index) => ({
    rosterId: `roster_${index + 1}`,
    name: `利用者 ${String(index + 1).padStart(2, "0")}`,
    email: `user${index + 1}@example.com`,
    role: index % 5 === 0 ? "コントラクトMR" : "本社MR",
    department: index % 5 === 0 ? "DM本社" : "DM専任",
    workplace: index % 5 === 0 ? "虎ノ門" : "大阪",
    area: index % 5 === 0 ? "本社" : "関西",
    areaKey: index % 5 === 0 ? "本社・虎ノ門" : "関西",
    labels: index % 4 === 0 ? [{ labelId: "label_1", name: "重点", color: "#23d28f" }] : [],
    lastActiveAt: index < 60 ? `2026-08-${String(23 - (index % 7)).padStart(2, "0")}T01:00:00Z` : "",
    activeDays7: index % 7,
    activeDaysInPeriod: index % 7,
    userMessageCountInPeriod: index % 13,
    userMessageCount7: index % 13,
    completeDelivery: measurement(index % 3 === 0 ? null : .9, index % 3 === 0 ? 0 : 9, 10),
    activity: ["high", "middle", "low", "dormant"][index % 4],
    activityLabel: ["高アクティブ", "中アクティブ", "低アクティブ", "休眠ユーザー"][index % 4],
  }));
}

function makeManagedUsers(count = 83) {
  return Array.from({ length: count }, (_, index) => ({
    rosterId: `roster_${index + 1}`,
    name: `利用者 ${String(index + 1).padStart(2, "0")}`,
    email: `user${index + 1}@example.com`,
    area: index % 5 === 0 ? "本社" : "関西",
    areaKey: index % 5 === 0 ? "本社・虎ノ門" : "関西",
    workplace: index % 5 === 0 ? "虎ノ門" : "大阪",
    role: index % 5 === 0 ? "本社メンバー" : "本社MR",
    department: index >= 80 ? "管理者" : (index % 5 === 0 ? "DM本社" : "DM専任"),
    mrExperience: index % 5 === 0 ? "-" : "10年",
    labelIds: index % 4 === 0 ? ["label_1"] : [],
    isActive: index % 11 !== 0,
    identityBound: index % 2 === 0,
    globalScopeEnabled: index < 80 && index % 5 !== 0 && index % 11 !== 0,
    userMapScopeEnabled: index < 80 && index % 11 !== 0,
    scopePolicyVersion: "summary_role_v1",
    rosterIssues: [],
    updatedAt: "2026-08-23T01:00:00Z",
    updatedBy: "admin@example.com",
  }));
}

async function installApiMocks(page, {
  failOverview = false,
  failOverviewUsers = false,
  failDetail = false,
  detailNotFound = false,
  failConversations = false,
  failManagedLabels = false,
  failScopePreview = false,
  managementUserConflict = false,
  invalidExportResponse = false,
  newsUsageOverride = {},
  failNewsUsage = false,
  overviewOverride = {},
  overviewByPreset = {},
  overviewDelayByPreset = {},
  overviewUsersDelayByQuery = {},
  overviewUsersByQuery = {},
  usersOverride = {},
  regionsOverride = {},
  detailOverride = {},
  managedUsersOverride = {},
  managedLabelsOverride = {},
  managementMetadataOverride = {},
  exportCreateDelay = 0,
  beforeExportCreateResponse = null,
  exportDownloadDelay = 0,
  exportDeleteDelay = 0,
  exportDownloadFailures = 0,
  requests = [],
} = {}) {
  let exportSequence = 0;
  let managementSequence = 0;
  let managedUserRows = structuredClone(managedUsersOverride.users ?? managedUsers.users);
  let managedLabelRows = structuredClone(managedLabelsOverride.labels ?? managedLabels.labels);
  let remainingExportDownloadFailures = Number(exportDownloadFailures || 0);
  const exportJobIdsByKey = new Map();
  const exportJobs = new Map();
  const wait = async (delay) => {
    const milliseconds = Number(delay || 0);
    if (milliseconds > 0) await new Promise((resolve) => setTimeout(resolve, milliseconds));
  };
  const csvCell = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;
  const exportCsv = (jobId, body) => {
    const columns = ["job_id", "q", "activity", "sort", "area", "preset", "idempotency_key"];
    const values = [jobId, body.q, body.activity, body.sort, body.areaKey, body.preset, body.idempotencyKey];
    return `\ufeff${columns.join(",")}\n${values.map(csvCell).join(",")}\n`;
  };
  await page.route(/\/api\/(analytics|trace|admin|export|news-usage)\//, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    requests.push({ method: request.method(), path: url.pathname, search: url.search, body: request.postDataJSON?.() });
    if (url.pathname.startsWith("/api/news-usage/")) {
      if (failNewsUsage) return route.fulfill({ status: 503, json: { detail: "News usage unavailable" } });
      const userScope = url.pathname.startsWith("/api/news-usage/users/");
      return route.fulfill({ json: { ...newsUsage, scope: userScope ? "user_map" : "global", rosterId: userScope ? url.pathname.split("/").at(-1) : "", ...newsUsageOverride } });
    }
    if (["/api/analytics/overview", "/api/analytics/environment", "/api/analytics/trend"].includes(url.pathname)) {
      const preset = url.searchParams.get("preset") || "last_7d";
      const delay = Number(overviewDelayByPreset[preset] || 0);
      if (delay > 0) await new Promise((resolve) => setTimeout(resolve, delay));
      return failOverview
        ? route.fulfill({ status: 503, json: { detail: { code: "source_unavailable", message: "集計停止" } } })
        : route.fulfill({ json: { ...overview, ...overviewOverride, ...(overviewByPreset[preset] || {}) } });
    }
    if (url.pathname === "/api/analytics/regions") return route.fulfill({ json: { ...regions, ...regionsOverride } });
    if (url.pathname === "/api/analytics/overview/users") {
      const query = url.searchParams.get("q") || "";
      const delay = Number(overviewUsersDelayByQuery[query] || 0);
      if (delay > 0) await new Promise((resolve) => setTimeout(resolve, delay));
      if (failOverviewUsers) return route.fulfill({ status: 503, json: { detail: { code: "source_unavailable", message: "ユーザー集計停止" } } });
      return route.fulfill({ json: { ...overviewUsers, ...usersOverride, ...(overviewUsersByQuery[query] || {}) } });
    }
    if (url.pathname === "/api/analytics/users") return route.fulfill({ json: { ...users, ...usersOverride } });
    if (url.pathname === "/api/analytics/users/roster_1") {
      if (detailNotFound) return route.fulfill({ status: 404, json: { detail: { code: "user_not_found", message: "user not found" } } });
      return failDetail
        ? route.fulfill({ status: 503, json: { detail: { code: "source_unavailable", message: "個人分析停止" } } })
        : route.fulfill({ json: { ...detail, ...detailOverride } });
    }
    if (url.pathname === "/api/trace/conversations") {
      if (detailNotFound) return route.fulfill({ status: 404, json: { detail: { code: "user_not_found", message: "user not found" } } });
      return failConversations
        ? route.fulfill({ status: 503, json: { detail: { code: "source_unavailable", message: "会話停止" } } })
        : route.fulfill({ json: conversations });
    }
    if (url.pathname === "/api/trace/messages") return route.fulfill({ json: { status: "ready", messages: [{ messageId: "m1", timestampJst: "2026-08-23 10:00:00", role: "user", roleLabel: "ユーザー", content: "製品の仕様を教えてください", mode: "internal", feedback: "none", status: "done" }, { messageId: "m2", timestampJst: "2026-08-23 10:00:05", role: "assistant", roleLabel: "アシスタント", content: "仕様を確認しました。", mode: "internal", feedback: "none", status: "done" }], page: { nextCursor: "" } } });
    if (url.pathname === "/api/admin/users" && request.method() === "GET") return route.fulfill({ json: { ...managedUsers, ...managedUsersOverride, users: managedUserRows } });
    if (url.pathname === "/api/admin/labels" && request.method() === "GET") return failManagedLabels
      ? route.fulfill({ status: 503, json: { detail: { code: "source_unavailable", message: "ラベル停止" } } })
      : route.fulfill({ json: { ...managedLabels, ...managedLabelsOverride, labels: managedLabelRows } });
    if (url.pathname === "/api/admin/metadata" && request.method() === "GET") return route.fulfill({ json: { ...managementMetadata, ...managementMetadataOverride } });
    if (url.pathname === "/api/admin/scope-preview" && request.method() === "POST") {
      if (failScopePreview) return route.fulfill({ status: 503, json: { detail: { code: "source_unavailable", message: "判定停止" } } });
      const body = request.postDataJSON();
      const userMapScopeEnabled = body.is_active && body.department !== "管理者";
      return route.fulfill({ status: 200, json: {
        globalScopeEnabled: userMapScopeEnabled && ["本社MR", "コントラクトMR"].includes(body.role),
        userMapScopeEnabled,
        scopePolicyVersion: "summary_role_v1",
      } });
    }
    if (url.pathname.startsWith("/api/admin/")) {
      if (managementUserConflict && request.method() === "PATCH" && url.pathname.includes("/users/")) {
        return route.fulfill({ status: 409, json: { detail: { code: "update_conflict", message: "user update conflict" } } });
      }
      const updatedAt = () => `2026-08-24T01:00:${String(++managementSequence).padStart(2, "0")}Z`;
      const userFields = (body, current = {}) => ({
        ...current,
        name: canonicalText(body.name),
        email: canonicalEmail(body.email),
        area: canonicalText(body.area),
        areaKey: body.area === "本社" ? "本社・虎ノ門" : body.area,
        workplace: canonicalText(body.workplace),
        role: canonicalText(body.role),
        department: canonicalText(body.department),
        mrExperience: canonicalText(body.mr_experience) || "-",
        labelIds: Object.hasOwn(body, "label_ids") ? [...body.label_ids] : [...(current.labelIds || [])],
        isActive: body.is_active,
        globalScopeEnabled: body.is_active && body.department !== "管理者" && ["本社MR", "コントラクトMR"].includes(body.role),
        userMapScopeEnabled: body.is_active && body.department !== "管理者",
        scopePolicyVersion: "summary_role_v1",
        rosterIssues: [],
        updatedAt: updatedAt(),
        updatedBy: "admin@example.com",
      });
      const userMatch = url.pathname.match(/^\/api\/admin\/users\/([^/]+)$/);
      if (userMatch && request.method() === "PATCH") {
        const rosterId = decodeURIComponent(userMatch[1]);
        const index = managedUserRows.findIndex((row) => row.rosterId === rosterId);
        const saved = userFields(request.postDataJSON(), managedUserRows[index] || { rosterId, identityBound: false });
        saved.rosterId = rosterId;
        saved.identityBound = Boolean(managedUserRows[index]?.identityBound);
        if (index >= 0) managedUserRows[index] = saved; else managedUserRows.push(saved);
        return route.fulfill({ status: 200, json: saved });
      }
      if (url.pathname === "/api/admin/users" && request.method() === "POST") {
        const saved = userFields(request.postDataJSON(), {
          rosterId: `roster_created_${managementSequence + 1}`,
          identityBound: false,
        });
        managedUserRows.push(saved);
        return route.fulfill({ status: 201, json: saved });
      }
      const labelMatch = url.pathname.match(/^\/api\/admin\/labels\/([^/]+)$/);
      if (labelMatch && request.method() === "DELETE") {
        const labelId = decodeURIComponent(labelMatch[1]);
        managedLabelRows = managedLabelRows.filter((row) => row.labelId !== labelId);
        return route.fulfill({ status: 204, body: "" });
      }
      if (labelMatch && request.method() === "PATCH") {
        const labelId = decodeURIComponent(labelMatch[1]);
        const body = request.postDataJSON();
        const index = managedLabelRows.findIndex((row) => row.labelId === labelId);
        const current = managedLabelRows[index] || { labelId, usageCount: 0, labelIssues: [] };
        const saved = {
          ...current,
          name: canonicalText(body.name),
          color: body.color,
          isActive: body.is_active,
          updatedAt: updatedAt(),
          updatedBy: "admin@example.com",
        };
        if (index >= 0) managedLabelRows[index] = saved; else managedLabelRows.push(saved);
        return route.fulfill({ status: 200, json: saved });
      }
      if (url.pathname === "/api/admin/labels" && request.method() === "POST") {
        const body = request.postDataJSON();
        const saved = {
          labelId: `label_created_${managementSequence + 1}`,
          name: canonicalText(body.name),
          color: body.color,
          usageCount: 0,
          isActive: true,
          labelIssues: [],
          updatedAt: updatedAt(),
          updatedBy: "admin@example.com",
        };
        managedLabelRows.push(saved);
        return route.fulfill({ status: 201, json: saved });
      }
      return route.fulfill({ status: 404, json: { detail: { code: "not_found", message: "admin route not mocked" } } });
    }
    if (url.pathname === "/api/export/jobs" && request.method() === "POST") {
      const body = request.postDataJSON();
      const idempotencyKey = String(body?.idempotencyKey || "");
      let jobId = exportJobIdsByKey.get(idempotencyKey);
      if (!jobId) {
        jobId = `job_${++exportSequence}`;
        exportJobIdsByKey.set(idempotencyKey, jobId);
      }
      const job = {
        jobId,
        body,
        csv: exportCsv(jobId, body),
        filename: `monitor-${jobId}.csv`,
      };
      exportJobs.set(jobId, job);
      await wait(exportCreateDelay);
      await beforeExportCreateResponse?.(job);
      return route.fulfill({ status: 201, json: invalidExportResponse
        ? { jobId, status: "ready", filename: job.filename, rowCount: 1, expiresAt: "2099-08-23T02:00:00Z", downloadUrl: "https://evil.invalid/export.csv" }
        : { jobId, status: "ready", filename: job.filename, rowCount: 1, expiresAt: "2099-08-23T02:00:00Z", downloadUrl: `/api/export/jobs/${jobId}/download` } });
    }
    const exportJobMatch = url.pathname.match(/^\/api\/export\/jobs\/([^/]+)(\/download)?$/);
    if (exportJobMatch && exportJobMatch[2] && request.method() === "GET") {
      await wait(exportDownloadDelay);
      if (remainingExportDownloadFailures > 0) {
        remainingExportDownloadFailures -= 1;
        return route.fulfill({ status: 503, json: { detail: { code: "source_unavailable", message: "CSV download failed" } } });
      }
      const job = exportJobs.get(exportJobMatch[1]);
      if (!job) return route.fulfill({ status: 404, json: { detail: { code: "not_found", message: "export missing" } } });
      return route.fulfill({ status: 200, contentType: "text/csv; charset=utf-8", body: job.csv });
    }
    if (exportJobMatch && !exportJobMatch[2] && request.method() === "DELETE") {
      await wait(exportDeleteDelay);
      exportJobs.delete(exportJobMatch[1]);
      return route.fulfill({ status: 204, body: "" });
    }
    return route.fulfill({ status: 404, json: { detail: { code: "not_found", message: "not mocked" } } });
  });
  return requests;
}

module.exports = {
  overview, users, overviewUsers, regions, detail, conversations, managedUsers, managedLabels,
  managementMetadata, makeAnalyticsUsers, makeManagedUsers, installApiMocks, newsUsage,
};
