const overview = {
  scope: "global", status: "ready", dataThrough: "2026-08-23T01:00:00Z",
  kpis: { activeUsers: 24, adoptionRate: 24 / 69, returnRate: .5, questionsPerActiveUser: 3.2, completeDeliveryRate: .91, p95LatencyMs: 72000 },
  hourlyQuestions: Array.from({ length: 24 }, (_, hour) => ({ hour: `${String(hour).padStart(2, "0")}:00`, count: hour + 1 })),
  deviceDistribution: [{ key: "desktop", label: "PC", count: 50, rate: .8 }, { key: "mobile", label: "モバイル", count: 12, rate: .2 }],
  modeDistribution: [{ key: "internal", label: "社内モード", count: 49, rate: .79 }, { key: "websearch", label: "Web検索モード", count: 13, rate: .21 }],
  usageTrend: [{ date: "2026-08-22", activeUsers: 18, questions: 49 }, { date: "2026-08-23", activeUsers: 20, questions: 62 }],
  questionCategories: [{ key: "product_information", count: 32, rate: .52 }, { key: "comparison_fit_selection", count: 20, rate: .32 }, { key: "unclassified", count: 10, rate: .16 }],
  activityDistribution: [{ key: "high", label: "高アクティブ", count: 10, rate: 10 / 69 }, { key: "middle", label: "中アクティブ", count: 14, rate: 14 / 69 }, { key: "low", label: "低アクティブ", count: 20, rate: 20 / 69 }, { key: "dormant", label: "休眠ユーザー", count: 25, rate: 25 / 69 }],
  activityByArea: [{ label: "関西", total: 10, segments: [{ key: "high", label: "高アクティブ", count: 3, rate: .3 }, { key: "middle", label: "中アクティブ", count: 2, rate: .2 }, { key: "low", label: "低アクティブ", count: 2, rate: .2 }, { key: "dormant", label: "休眠ユーザー", count: 3, rate: .3 }] }],
  activityByRole: [{ label: "本社MR", total: 10, segments: [{ key: "high", label: "高アクティブ", count: 3, rate: .3 }, { key: "middle", label: "中アクティブ", count: 2, rate: .2 }, { key: "low", label: "低アクティブ", count: 2, rate: .2 }, { key: "dormant", label: "休眠ユーザー", count: 3, rate: .3 }] }],
  topProducts: [{ label: "テルフュージョン", count: 28 }, { label: "ケモセーフ", count: 17 }],
  productQuestionMatrix: [{ product: "テルフュージョン", category: "product_information", count: 16 }, { product: "テルフュージョン", category: "comparison_fit_selection", count: 12 }],
  productResolution: { candidateCount: 45, resolvedCount: 45, unresolvedQuestions: 0, resolutionRate: 1 },
};

const users = { status: "ready", dataThrough: overview.dataThrough, users: [
  { rosterId: "roster_1", name: "山田 太郎", email: "user1@example.com", area: "関西", areaKey: "関西", labels: [{ labelId: "label_1", name: "重点", color: "#23d28f" }], lastActiveAt: "2026-08-23T01:00:00Z", activeDays7: 4, questionCount7: 12, completeDeliveryRate: .92, activity: "high", activityLabel: "高アクティブ" },
  { rosterId: "roster_2", name: "佐藤 花子", email: "user2@example.com", area: "本社", areaKey: "本社・虎ノ門", labels: [], lastActiveAt: "", activeDays7: 0, questionCount7: 0, completeDeliveryRate: null, activity: "dormant", activityLabel: "休眠ユーザー" },
] };

const regions = { status: "ready", dataThrough: overview.dataThrough, regions: [
  { areaKey: "関西", area: "関西", rosterUsers: 10, activeUsers: 6, questions: 42, adoptionRate: .6, returnRate: .5 },
  { areaKey: "本社・虎ノ門", area: "本社・虎ノ門", rosterUsers: 19, activeUsers: 8, questions: 35, adoptionRate: 8 / 19, returnRate: .375 },
] };

const detail = { status: "ready", dataThrough: overview.dataThrough,
  profile: { rosterId: "roster_1", name: "山田 太郎", email: "user1@example.com", area: "関西", workplace: "大阪", role: "本社MR", department: "DM専任", mrExperience: "10年", labels: users.users[0].labels },
  summary: { lastActiveAt: "2026-08-23T01:00:00Z", activeDays: 5, questions: 20, questionsPerActiveDay: 4, completeDeliveryRate: .9 },
  comparisons: { area: { label: "関西", peerCount: 10, averageQuestions: 8.2, averageActiveDays: 3.1, averageCompleteDeliveryRate: .84 }, role: { label: "本社MR", peerCount: 39, averageQuestions: 7.3, averageActiveDays: 2.8, averageCompleteDeliveryRate: .86 } },
  trend: [{ date: "2026-08-22", questions: 7, completeDeliveryRate: .86 }, { date: "2026-08-23", questions: 13, completeDeliveryRate: .92 }],
  products: [{ label: "テルフュージョン", count: 12 }], tasks: [{ key: "fact_lookup", count: 4 }],
  productResolution: { candidateCount: 12, resolvedCount: 12, unresolvedQuestions: 0, resolutionRate: 1 },
  questionCategories: [{ key: "product_information", count: 12, rate: .6 }],
  modes: [{ label: "社内モード", count: 18, rate: .9 }], devices: [{ label: "PC", count: 16, rate: .8 }],
  conversations: [{ conversationId: "conv_1", title: "製品情報の確認", messageCount: 4, updatedAtJst: "2026-08-23 10:00:00" }],
};

const managedUsers = { users: [{ rosterId: "roster_1", name: "山田 太郎", email: "user1@example.com", area: "関西", areaKey: "関西", workplace: "大阪", role: "本社MR", department: "DM専任", mrExperience: "10年", labelIds: ["label_1"], isActive: true, updatedAt: "2026-08-23T01:00:00Z", updatedBy: "admin@example.com" }] };
const managedLabels = { labels: [{ labelId: "label_1", name: "重点", color: "#23d28f", usageCount: 1, isActive: true, updatedAt: "2026-08-23T01:00:00Z", updatedBy: "admin@example.com" }] };

async function installApiMocks(page, { failOverview = false, overviewOverride = {}, usersOverride = {}, requests = [] } = {}) {
  await page.route(/\/api\/(analytics|trace|admin|export)\//, async (route) => {
    const request = route.request(); const url = new URL(request.url()); requests.push({ method: request.method(), path: url.pathname, search: url.search, body: request.postDataJSON?.() });
    if (url.pathname === "/api/analytics/overview") return failOverview ? route.fulfill({ status: 503, json: { detail: "集計停止" } }) : route.fulfill({ json: { ...overview, ...overviewOverride } });
    if (url.pathname === "/api/analytics/regions") return route.fulfill({ json: regions });
    if (url.pathname === "/api/analytics/users") return route.fulfill({ json: { ...users, ...usersOverride } });
    if (url.pathname === "/api/analytics/users/roster_1") return route.fulfill({ json: detail });
    if (url.pathname === "/api/trace/messages") return route.fulfill({ json: { status: "ready", messages: [{ messageId: "m1", timestampJst: "2026-08-23 10:00:00", role: "user", roleLabel: "ユーザー", content: "製品の仕様を教えてください", mode: "internal", feedback: "none", status: "done" }, { messageId: "m2", timestampJst: "2026-08-23 10:00:05", role: "assistant", roleLabel: "アシスタント", content: "仕様を確認しました。", mode: "internal", feedback: "none", status: "done" }], page: { nextCursor: "" } } });
    if (url.pathname === "/api/admin/users" && request.method() === "GET") return route.fulfill({ json: managedUsers });
    if (url.pathname === "/api/admin/labels" && request.method() === "GET") return route.fulfill({ json: managedLabels });
    if (url.pathname.startsWith("/api/admin/")) {
      if (request.method() === "DELETE") return route.fulfill({ status: 204, body: "" });
      return route.fulfill({ status: 200, json: {} });
    }
    if (url.pathname === "/api/export/jobs") return route.fulfill({ status: 201, json: { jobId: "job_1", filename: "monitor.csv", downloadUrl: "/api/export/jobs/job_1/download" } });
    return route.fulfill({ status: 404, json: { detail: "not mocked" } });
  });
  return requests;
}

module.exports = { overview, users, regions, detail, managedUsers, managedLabels, installApiMocks };
