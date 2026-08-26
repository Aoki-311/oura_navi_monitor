const freshness = { state: "fresh", dataThrough: "2026-08-23T01:00:00Z" };
const measurementState = (measuredCount, totalCount) => {
  if (totalCount === 0) return "no_usage";
  if (measuredCount === 0) return "not_measured";
  if (measuredCount < totalCount) return "partial";
  return "measured";
};
const measurement = (value, measuredCount, totalCount) => ({
  value, measuredCount, totalCount,
  measurementState: measurementState(measuredCount, totalCount),
});

const overview = {
  scope: "global",
  scopeUserCount: 69,
  freshness,
  kpis: {
    activeUsers: 24,
    adoptionRate: 24 / 69,
    returnRate: .5,
    questionsPerActiveUser: 3.2,
    completeDelivery: measurement(.91, 70, 77),
    p95Latency: { valueMs: 72000, measuredCount: 75, totalCount: 77, measurementState: "partial" },
  },
  hourlyQuestions: Array.from({ length: 24 }, (_, hour) => ({ hour: `${String(hour).padStart(2, "0")}:00`, count: hour + 1 })),
  deviceDistribution: [{ key: "desktop", label: "PC", count: 50, rate: .8 }, { key: "mobile", label: "モバイル", count: 12, rate: .2 }],
  deviceMeasurement: { measuredCount: 62, totalCount: 77, measurementState: "partial" },
  modeDistribution: [{ key: "internal", label: "社内モード", count: 49, rate: .79 }, { key: "websearch", label: "Web検索モード", count: 13, rate: .21 }],
  modeMeasurement: { measuredCount: 62, totalCount: 77, measurementState: "partial" },
  usageTrend: [{ date: "2026-08-22", activeUsers: 18, questions: 49 }, { date: "2026-08-23", activeUsers: 20, questions: 62 }],
  requestTasks: [{ key: "fact_lookup", label: "情報確認", count: 32, rate: .52 }, { key: "comparison_selection", label: "比較・選定", count: 20, rate: .32 }, { key: "unclassified", label: "判定不能", count: 10, rate: .16 }],
  taskMeasurement: { measuredCount: 62, totalCount: 77, measurementState: "partial" },
  activityDistribution: [{ key: "high", label: "高アクティブ", count: 10, rate: 10 / 69 }, { key: "middle", label: "中アクティブ", count: 14, rate: 14 / 69 }, { key: "low", label: "低アクティブ", count: 20, rate: 20 / 69 }, { key: "dormant", label: "休眠ユーザー", count: 25, rate: 25 / 69 }],
  activityByArea: [{ label: "関西", total: 10, segments: [{ key: "high", label: "高アクティブ", count: 3, rate: .3 }, { key: "middle", label: "中アクティブ", count: 2, rate: .2 }, { key: "low", label: "低アクティブ", count: 2, rate: .2 }, { key: "dormant", label: "休眠ユーザー", count: 3, rate: .3 }] }],
  activityByRole: [{ label: "本社MR", total: 10, segments: [{ key: "high", label: "高アクティブ", count: 3, rate: .3 }, { key: "middle", label: "中アクティブ", count: 2, rate: .2 }, { key: "low", label: "低アクティブ", count: 2, rate: .2 }, { key: "dormant", label: "休眠ユーザー", count: 3, rate: .3 }] }],
  topProducts: [{ label: "テルフュージョン", count: 28 }, { label: "ケモセーフ", count: 17 }],
  productTaskMatrix: [{ product: "テルフュージョン", task: "fact_lookup", taskLabel: "情報確認", count: 16 }, { product: "テルフュージョン", task: "comparison_selection", taskLabel: "比較・選定", count: 12 }],
  productResolution: { candidateCount: 45, resolvedCount: 45, unresolvedQuestions: 0, resolutionRate: 1, measuredCount: 45, totalCount: 77, measurementState: "partial" },
};

const users = { scopeUserCount: 80, freshness, users: [
  { rosterId: "roster_1", name: "山田 太郎", email: "user1@example.com", area: "関西", areaKey: "関西", labels: [{ labelId: "label_1", name: "重点", color: "#23d28f" }], lastActiveAt: "2026-08-23T01:00:00Z", activeDays7: 4, userMessageCount7: 12, completeDelivery: measurement(.92, 11, 12), activity: "high", activityLabel: "高アクティブ" },
  { rosterId: "roster_2", name: "佐藤 花子", email: "user2@example.com", area: "本社", areaKey: "本社・虎ノ門", labels: [], lastActiveAt: "", activeDays7: 0, userMessageCount7: 0, completeDelivery: measurement(null, 0, 0), activity: "dormant", activityLabel: "休眠ユーザー" },
] };

const regions = { scopeUserCount: 80, freshness, regions: [
  { areaKey: "関西", area: "関西", rosterUsers: 10, activeUsers: 6, questions: 42, adoptionRate: .6, returnRate: .5 },
  { areaKey: "本社・虎ノ門", area: "本社・虎ノ門", rosterUsers: 19, activeUsers: 8, questions: 35, adoptionRate: 8 / 19, returnRate: .375 },
] };

const detail = {
  freshness,
  profile: { rosterId: "roster_1", name: "山田 太郎", email: "user1@example.com", area: "関西", workplace: "大阪", role: "本社MR", department: "DM専任", mrExperience: "10年", labels: users.users[0].labels },
  summary: { lastActiveAt: "2026-08-23T01:00:00Z", activeDays: 5, questions: 20, questionsPerActiveDay: 4, completeDelivery: measurement(.9, 18, 20) },
  comparisons: {
    area: { label: "関西", peerCount: 10, averageQuestions: 8.2, averageActiveDays: 3.1, averageCompleteDelivery: measurement(.84, 8, 10) },
    role: { label: "本社MR", peerCount: 39, averageQuestions: 7.3, averageActiveDays: 2.8, averageCompleteDelivery: measurement(.86, 30, 39) },
  },
  trend: [{ date: "2026-08-22", questions: 7, completeDelivery: measurement(.86, 7, 7) }, { date: "2026-08-23", questions: 13, completeDelivery: measurement(.92, 11, 13) }],
  products: [{ label: "テルフュージョン", count: 12 }],
  tasks: [{ key: "fact_lookup", label: "情報確認", count: 4, rate: .2 }],
  productResolution: { candidateCount: 12, resolvedCount: 12, unresolvedQuestions: 0, resolutionRate: 1, measuredCount: 12, totalCount: 20, measurementState: "partial" },
  questionCategories: [{ key: "product_information", label: "製品情報・仕様", count: 12, rate: .6 }],
  questionCategoryMeasurement: { measuredCount: 12, totalCount: 20, measurementState: "partial" },
  taskMeasurement: { measuredCount: 4, totalCount: 20, measurementState: "partial" },
  modes: [{ key: "internal", label: "社内モード", count: 18, rate: .9 }],
  modeMeasurement: { measuredCount: 18, totalCount: 20, measurementState: "partial" },
  devices: [{ key: "desktop", label: "PC", count: 16, rate: .8 }],
  deviceMeasurement: { measuredCount: 16, totalCount: 20, measurementState: "partial" },
};

const conversations = { status: "ready", conversations: [{ conversationId: "conv_1", title: "製品情報の確認", messageCount: 4, updatedAt: "2026-08-23T01:00:00Z", updatedAtJst: "2026-08-23 10:00:00" }] };
const managedUsers = { users: [{ rosterId: "roster_1", name: "山田 太郎", email: "user1@example.com", area: "関西", areaKey: "関西", workplace: "大阪", role: "本社MR", department: "DM専任", mrExperience: "10年", labelIds: ["label_1"], isActive: true, identityBound: true, globalScopeEnabled: true, userMapScopeEnabled: true, updatedAt: "2026-08-23T01:00:00Z", updatedBy: "admin@example.com" }] };
const managedLabels = { labels: [{ labelId: "label_1", name: "重点", color: "#23d28f", usageCount: 1, isActive: true, updatedAt: "2026-08-23T01:00:00Z", updatedBy: "admin@example.com" }] };
const managementMetadata = { areas: ["北海道東北", "関東A", "関東B", "首都圏A", "首都圏B", "東海北陸", "関西", "中四国", "九州", "本社"], workplaces: ["大阪", "虎ノ門"], roles: ["本社MR", "本社メンバー"], departments: ["DM専任", "ヘルスケア本社", "DM本社", "管理者"], departmentScopes: [{ department: "DM専任", globalScopeEnabled: true, userMapScopeEnabled: true }, { department: "ヘルスケア本社", globalScopeEnabled: true, userMapScopeEnabled: true }, { department: "DM本社", globalScopeEnabled: false, userMapScopeEnabled: true }, { department: "管理者", globalScopeEnabled: false, userMapScopeEnabled: false }], labelColors: ["#23d28f", "#386dff", "#ffb340", "#ff5b74", "#7c5cff", "#27d9d2", "#5f6285"] };

function makeAnalyticsUsers(count = 80) {
  return Array.from({ length: count }, (_, index) => ({
    rosterId: `roster_${index + 1}`,
    name: `利用者 ${String(index + 1).padStart(2, "0")}`,
    email: `user${index + 1}@example.com`,
    area: index % 5 === 0 ? "本社" : "関西",
    areaKey: index % 5 === 0 ? "本社・虎ノ門" : "関西",
    labels: index % 4 === 0 ? [{ labelId: "label_1", name: "重点", color: "#23d28f" }] : [],
    lastActiveAt: index < 60 ? `2026-08-${String(23 - (index % 7)).padStart(2, "0")}T01:00:00Z` : "",
    activeDays7: index % 7,
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
    updatedAt: "2026-08-23T01:00:00Z",
    updatedBy: "admin@example.com",
  }));
}

async function installApiMocks(page, {
  failOverview = false,
  failDetail = false,
  detailNotFound = false,
  failConversations = false,
  managementUserConflict = false,
  overviewOverride = {},
  overviewByPreset = {},
  overviewDelayByPreset = {},
  usersOverride = {},
  detailOverride = {},
  managedUsersOverride = {},
  managedLabelsOverride = {},
  managementMetadataOverride = {},
  requests = [],
} = {}) {
  await page.route(/\/api\/(analytics|trace|admin|export)\//, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    requests.push({ method: request.method(), path: url.pathname, search: url.search, body: request.postDataJSON?.() });
    if (url.pathname === "/api/analytics/overview") {
      const preset = url.searchParams.get("preset") || "last_7d";
      const delay = Number(overviewDelayByPreset[preset] || 0);
      if (delay > 0) await new Promise((resolve) => setTimeout(resolve, delay));
      return failOverview
        ? route.fulfill({ status: 503, json: { detail: { code: "source_unavailable", message: "集計停止" } } })
        : route.fulfill({ json: { ...overview, ...overviewOverride, ...(overviewByPreset[preset] || {}) } });
    }
    if (url.pathname === "/api/analytics/regions") return route.fulfill({ json: regions });
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
    if (url.pathname === "/api/admin/users" && request.method() === "GET") return route.fulfill({ json: { ...managedUsers, ...managedUsersOverride } });
    if (url.pathname === "/api/admin/labels" && request.method() === "GET") return route.fulfill({ json: { ...managedLabels, ...managedLabelsOverride } });
    if (url.pathname === "/api/admin/metadata" && request.method() === "GET") return route.fulfill({ json: { ...managementMetadata, ...managementMetadataOverride } });
    if (url.pathname.startsWith("/api/admin/")) {
      if (managementUserConflict && request.method() === "PATCH" && url.pathname.includes("/users/")) {
        return route.fulfill({ status: 409, json: { detail: { code: "update_conflict", message: "user update conflict" } } });
      }
      if (request.method() === "DELETE") return route.fulfill({ status: 204, body: "" });
      if (url.pathname.includes("/users/")) return route.fulfill({ status: 200, json: managedUsers.users[0] });
      if (url.pathname === "/api/admin/users") return route.fulfill({ status: 201, json: managedUsers.users[0] });
      if (url.pathname.includes("/labels/")) return route.fulfill({ status: 200, json: managedLabels.labels[0] });
      return route.fulfill({ status: 201, json: managedLabels.labels[0] });
    }
    if (url.pathname === "/api/export/jobs") return route.fulfill({ status: 201, json: { jobId: "job_1", filename: "monitor.csv", downloadUrl: "/api/export/jobs/job_1/download" } });
    return route.fulfill({ status: 404, json: { detail: { code: "not_found", message: "not mocked" } } });
  });
  return requests;
}

module.exports = {
  overview, users, regions, detail, conversations, managedUsers, managedLabels,
  managementMetadata, makeAnalyticsUsers, makeManagedUsers, installApiMocks,
};
