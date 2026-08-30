const { test, expect } = require("@playwright/test");
const {
  installApiMocks, makeAnalyticsUsers, overview, overviewUsers, regions,
} = require("./fixtures");

const snapshotReceipt = (suffix) => ({
  scopePolicyVersion: "summary_role_v1",
  rosterFingerprint: `roster-${suffix}`,
  contentFingerprint: `content-${suffix}`,
  publishedRunId: `run-${suffix}`,
});

const legacyReceipt = {
  scopePolicyVersion: null,
  rosterFingerprint: null,
  contentFingerprint: null,
  publishedRunId: null,
  windowStart: null,
  windowEnd: null,
  windowTimezone: null,
};

test("overview renders seven analytics modules and preserves charts after refresh", async ({ page }) => {
  await installApiMocks(page);
  await page.goto("/dashboard");
  for (const title of ["主要KPI", "利用環境・モード", "利用推移", "活性度分布", "ユーザー一覧", "日本利用マップ", "製品ニーズ"]) await expect(page.locator("main")).toContainText(title);
  await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
  await expect(page.locator('[data-module="usage"]')).toContainText("途中集計");
  await expect(page.locator("#overviewUsers tbody tr")).toHaveCount(2);
  await expect(page.locator('[data-module="tasks"] h3')).toHaveText("質問種類");
  await expect(page.locator("main")).toContainText("製品 × 質問種類");
  await expect(page.locator("main")).not.toContainText("依頼タイプ");
  await expect(page.locator("main")).not.toContainText("質問の目的");
  expect(await page.locator("canvas").count()).toBeGreaterThanOrEqual(9);
  for (let index = 0; index < 6; index += 1) await page.getByRole("button", { name: "再読込" }).click();
  await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
});

test("manual refresh keeps the committed DOM mounted until a failed transaction settles", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, { requests });
  let failRefresh = false;
  let pendingRequests = 0;
  const refreshAnchors = [];
  let releaseRefresh;
  const refreshGate = new Promise((resolve) => { releaseRefresh = resolve; });
  await page.route(/\/api\/analytics\/(?:overview(?:\/users)?|regions)(?:\?.*)?$/, async (route) => {
    if (!failRefresh) return route.fallback();
    pendingRequests += 1;
    refreshAnchors.push(new URL(route.request().url()).searchParams.get("as_of"));
    await refreshGate;
    return route.fulfill({
      status: 503,
      json: { detail: { code: "source_unavailable", message: "refresh unavailable" } },
    });
  });
  await page.goto("/dashboard");
  await expect(page.locator('[data-module="kpis"] .kpiCard').first()).toContainText("24");
  const initialSnapshotRequests = requests.filter((row) => [
    "/api/analytics/overview", "/api/analytics/regions", "/api/analytics/overview/users",
  ].includes(row.path));
  expect(initialSnapshotRequests).toHaveLength(3);
  const initialAnchors = initialSnapshotRequests.map((row) => new URLSearchParams(row.search).get("as_of"));
  expect(initialAnchors.every(Boolean)).toBeTruthy();
  expect(new Set(initialAnchors).size).toBe(1);
  await page.locator('[data-module="kpis"]').evaluate((element) => { element.dataset.committedDom = "kept"; });

  failRefresh = true;
  await page.getByRole("button", { name: "再読込" }).click();
  await expect.poll(() => pendingRequests).toBe(3);
  expect(refreshAnchors.every(Boolean)).toBeTruthy();
  expect(new Set(refreshAnchors).size).toBe(1);
  await expect(page.locator('[data-module="kpis"]')).toHaveAttribute("data-committed-dom", "kept");
  await expect(page.locator("#usageChart")).toHaveCount(1);
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");

  releaseRefresh();
  await expect(page.locator("[data-freshness-banner] [data-overview-refresh-error]")).toHaveText(
    "最新データを取得できませんでした。表示中の内容を保持しています。",
  );
  await expect(page.locator('[data-module="kpis"]')).toHaveAttribute("data-committed-dom", "kept");
  await expect(page.locator('[data-module="kpis"] .kpiCard').first()).toContainText("24");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

test("page changes made during a full snapshot refresh commit on the latest page", async ({ page }) => {
  const requests = [];
  const initialUsers = makeAnalyticsUsers(80);
  const refreshedUsers = makeAnalyticsUsers(80).map((user, index) => ({
    ...user,
    name: `全量更新 ${String(index + 1).padStart(2, "0")}`,
  }));
  await installApiMocks(page, { requests, usersOverride: { users: initialUsers } });
  let refreshStarted = false;
  let pendingRefreshResponses = 0;
  let releaseRefresh;
  const refreshGate = new Promise((resolve) => { releaseRefresh = resolve; });
  await page.route(/\/api\/analytics\/(?:overview(?:\/users)?|regions)(?:\?.*)?$/, async (route) => {
    if (!refreshStarted) return route.fallback();
    pendingRefreshResponses += 1;
    await refreshGate;
    const path = new URL(route.request().url()).pathname;
    const receipt = snapshotReceipt("page-refresh");
    if (path === "/api/analytics/overview") {
      return route.fulfill({
        json: { ...overview, ...receipt, kpis: { ...overview.kpis, activeUsers: 42 } },
      });
    }
    if (path === "/api/analytics/regions") {
      return route.fulfill({
        json: {
          ...regions,
          ...receipt,
          regions: [{ ...regions.regions[0], area: "全量更新 関西" }],
        },
      });
    }
    return route.fulfill({
      json: { ...overviewUsers, ...receipt, users: refreshedUsers },
    });
  });

  await page.goto("/dashboard");
  await expect(page.locator('[data-module="users"]')).toContainText("1–15 / 80名");
  refreshStarted = true;
  await page.getByRole("button", { name: "再読込" }).click();
  await expect.poll(() => pendingRefreshResponses).toBe(3);
  await page.getByRole("button", { name: "次のページ" }).click();
  await expect(page).toHaveURL(/overview_page=2/);
  await expect(page.locator('[data-module="users"]')).toContainText("16–30 / 80名");

  releaseRefresh();
  await expect(page.locator("#overviewUsers tbody tr").first()).toContainText("全量更新 16");
  await expect(page.locator('[data-module="kpis"] .kpiCard').first()).toContainText("42");
  await expect(page.locator("#regionRanking")).toContainText("全量更新 関西");
  await expect(page).toHaveURL(/overview_page=2/);
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "CSV" }).click();
  await downloadPromise;
  expect(requests.filter((row) => (
    row.method === "POST" && row.path === "/api/export/jobs"
  )).at(-1)?.body).toMatchObject({
    expectedPublishedRunId: "run-page-refresh",
    expectedRosterFingerprint: "roster-page-refresh",
    expectedContentFingerprint: "content-page-refresh",
    expectedScopePolicyVersion: "summary_role_v1",
    expectedWindowStart: "2026-08-16T15:00:00Z",
    expectedWindowEnd: "2026-08-23T01:00:00Z",
    expectedWindowTimezone: "Asia/Tokyo",
  });
});

test("a shrinking full snapshot clamps both the visible page and its URL", async ({ page }) => {
  await installApiMocks(page, { usersOverride: { users: makeAnalyticsUsers(80) } });
  let shrinkStarted = false;
  let pendingShrinkResponses = 0;
  let releaseShrink;
  const shrinkGate = new Promise((resolve) => { releaseShrink = resolve; });
  await page.route(/\/api\/analytics\/(?:overview(?:\/users)?|regions)(?:\?.*)?$/, async (route) => {
    if (!shrinkStarted) return route.fallback();
    pendingShrinkResponses += 1;
    await shrinkGate;
    const path = new URL(route.request().url()).pathname;
    const receipt = snapshotReceipt("shrunk");
    if (path === "/api/analytics/overview") {
      return route.fulfill({ json: { ...overview, ...receipt } });
    }
    if (path === "/api/analytics/regions") {
      return route.fulfill({ json: { ...regions, ...receipt } });
    }
    return route.fulfill({
      json: { ...overviewUsers, ...receipt, users: makeAnalyticsUsers(10) },
    });
  });

  await page.goto("/dashboard?overview_page=6");
  await expect(page.locator('[data-module="users"]')).toContainText("76–80 / 80名");
  shrinkStarted = true;
  await page.getByRole("button", { name: "再読込" }).click();
  await expect.poll(() => pendingShrinkResponses).toBe(3);
  releaseShrink();

  await expect(page.locator('[data-module="users"]')).toContainText("1–10 / 10名");
  await expect(page.locator("#overviewUsers tbody tr").first()).toContainText("利用者 01");
  await expect(page).not.toHaveURL(/overview_page=/);
  await expect(page.getByRole("button", { name: "CSV" })).toBeEnabled();
});

test("a failed preset transaction restores the committed preset and keeps every module body", async ({ page }) => {
  await installApiMocks(page, {
    overviewByPreset: { last_14d: { kpis: { ...overview.kpis, activeUsers: 14 } } },
  });
  await page.route(/\/api\/analytics\/regions(?:\?.*)?$/, async (route) => {
    const selectedPreset = new URL(route.request().url()).searchParams.get("preset");
    if (selectedPreset !== "last_14d") return route.fallback();
    return route.fulfill({
      status: 503,
      json: { detail: { code: "source_unavailable", message: "regions unavailable" } },
    });
  });
  await page.goto("/dashboard");
  await expect(page.locator('[data-module="kpis"] .kpiCard').first()).toContainText("24");
  await expect(page.locator("#regionRanking")).toContainText("関西");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");

  await page.locator("#analysisPreset").selectOption("last_14d");
  await expect(page.locator("[data-freshness-banner] [data-overview-refresh-error]")).toHaveText(
    "最新データを取得できませんでした。表示中の内容を保持しています。",
  );
  await expect(page.locator("#analysisPreset")).toHaveValue("last_7d");
  await expect(page).not.toHaveURL(/preset=last_14d/);
  await expect(page.locator('[data-module="kpis"] .kpiCard').first()).toContainText("24");
  await expect(page.locator('[data-module="kpis"]')).not.toContainText("14");
  await expect(page.locator("#regionRanking")).toContainText("関西");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

test("a failed area transaction restores the committed area without clearing the overview", async ({ page }) => {
  await installApiMocks(page);
  await page.route(/\/api\/analytics\/overview(?:\?.*)?$/, async (route) => {
    const areaKey = new URL(route.request().url()).searchParams.get("area_key");
    if (areaKey !== "関西") return route.fallback();
    return route.fulfill({
      status: 503,
      json: { detail: { code: "source_unavailable", message: "overview unavailable" } },
    });
  });
  await page.goto("/dashboard");
  await expect(page.locator('[data-module="kpis"] .kpiCard').first()).toContainText("24");

  await page.locator('.rankingRow[data-area="関西"]').click();
  await expect(page.locator("[data-freshness-banner] [data-overview-refresh-error]")).toHaveText(
    "最新データを取得できませんでした。表示中の内容を保持しています。",
  );
  await expect(page).not.toHaveURL(/area=/);
  await expect(page.locator("#areaChip")).toBeEmpty();
  await expect(page.locator('.rankingRow[data-area="関西"]')).toHaveAttribute("aria-pressed", "false");
  await expect(page.locator('[data-module="kpis"] .kpiCard').first()).toContainText("24");
  await expect(page.locator("#regionRanking")).toContainText("関西");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

test("an expired filter anchor refreshes all Summary modules with one new anchor", async ({ page }) => {
  const observedRequests = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (["/api/analytics/overview", "/api/analytics/regions", "/api/analytics/overview/users"].includes(url.pathname)) {
      observedRequests.push({ path: url.pathname, search: url.search });
    }
  });
  await installApiMocks(page);
  await page.goto("/dashboard");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  expect(observedRequests).toHaveLength(3);
  const committedAnchor = new URLSearchParams(observedRequests[0].search).get("as_of");
  expect(committedAnchor).toBeTruthy();

  await page.clock.setFixedTime(new Date(Date.parse(committedAnchor) + 5 * 60 * 1000));
  await page.locator("#overviewUserSearch").fill("expired-anchor");
  await expect.poll(() => observedRequests.filter((row) => row.path === "/api/analytics/overview").length).toBe(2);
  await expect.poll(() => observedRequests.filter((row) => row.path === "/api/analytics/regions").length).toBe(2);
  await expect.poll(() => observedRequests.filter((row) => row.path === "/api/analytics/overview/users").length).toBe(2);

  const refreshedRequests = observedRequests.slice(3);
  expect(refreshedRequests).toHaveLength(3);
  const refreshedAnchors = refreshedRequests.map((row) => new URLSearchParams(row.search).get("as_of"));
  expect(refreshedAnchors.every(Boolean)).toBeTruthy();
  expect(new Set(refreshedAnchors).size).toBe(1);
  expect(refreshedAnchors[0]).not.toBe(committedAnchor);
  expect(new URLSearchParams(
    refreshedRequests.find((row) => row.path === "/api/analytics/overview/users").search,
  ).get("q")).toBe("expired-anchor");
  await expect(page.locator('[data-module="kpis"] .kpiCard').first()).toContainText("24");
  await expect(page.locator("#regionRanking")).toContainText("関西");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.getByRole("button", { name: "CSV" })).toBeEnabled();
});

test("a failed expired-anchor transaction keeps the committed Summary and disables CSV", async ({ page }) => {
  const observedRequests = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (["/api/analytics/overview", "/api/analytics/regions", "/api/analytics/overview/users"].includes(url.pathname)) {
      observedRequests.push({ path: url.pathname, search: url.search });
    }
  });
  let failRegions = false;
  await installApiMocks(page);
  await page.route(/\/api\/analytics\/regions(?:\?.*)?$/, async (route) => {
    if (!failRegions) return route.fallback();
    return route.fulfill({
      status: 503,
      json: { detail: { code: "source_unavailable", message: "regions unavailable" } },
    });
  });
  await page.goto("/dashboard");
  await expect(page.locator('[data-module="kpis"] .kpiCard').first()).toContainText("24");
  const committedAnchor = new URLSearchParams(observedRequests[0].search).get("as_of");
  await page.locator('[data-module="kpis"]').evaluate((element) => { element.dataset.expiredAnchorDom = "kept"; });

  failRegions = true;
  await page.clock.setFixedTime(new Date(Date.parse(committedAnchor) + 5 * 60 * 1000));
  await page.locator("#overviewUserSearch").fill("expired-failure");
  await expect(page.locator("[data-freshness-banner] [data-overview-refresh-error]")).toHaveText(
    "最新データを取得できませんでした。表示中の内容を保持しています。",
  );
  const refreshedRequests = observedRequests.slice(3);
  expect(refreshedRequests).toHaveLength(3);
  expect(new Set(refreshedRequests.map((row) => new URLSearchParams(row.search).get("as_of"))).size).toBe(1);
  expect(new URLSearchParams(refreshedRequests[0].search).get("as_of")).not.toBe(committedAnchor);
  await expect(page.locator('[data-module="kpis"]')).toHaveAttribute("data-expired-anchor-dom", "kept");
  await expect(page.locator('[data-module="kpis"] .kpiCard').first()).toContainText("24");
  await expect(page.locator("#regionRanking")).toContainText("関西");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

test("a malformed refreshed overview payload is rejected before any committed module changes", async ({ page }) => {
  let malformedRefresh = false;
  await installApiMocks(page);
  await page.route(/\/api\/analytics\/overview(?:\?.*)?$/, async (route) => {
    if (!malformedRefresh) return route.fallback();
    return route.fulfill({ json: { ...overview, kpis: null } });
  });
  await page.goto("/dashboard");
  await expect(page.locator('[data-module="kpis"] .kpiCard').first()).toContainText("24");
  await page.locator('[data-module="kpis"]').evaluate((element) => { element.dataset.adapterDom = "kept"; });

  malformedRefresh = true;
  await page.getByRole("button", { name: "再読込" }).click();
  await expect(page.locator("[data-freshness-banner] [data-overview-refresh-error]")).toHaveText(
    "最新データを取得できませんでした。表示中の内容を保持しています。",
  );
  await expect(page.locator('[data-module="kpis"]')).toHaveAttribute("data-adapter-dom", "kept");
  await expect(page.locator('[data-module="kpis"] .kpiCard').first()).toContainText("24");
  await expect(page.locator("#regionRanking")).toContainText("関西");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

test("a failed staged map refresh keeps the committed SVG and disables CSV", async ({ page }) => {
  let failMapRefresh = false;
  await installApiMocks(page);
  await page.route(/\/dashboard-assets\/assets\/japan-regions\.svg(?:\?.*)?$/, async (route) => {
    if (!failMapRefresh) return route.fallback();
    return route.fulfill({ status: 503, body: "map unavailable" });
  });
  await page.goto("/dashboard");
  await expect(page.locator("#japanMap svg")).toHaveCount(1);
  await page.locator("#japanMap").evaluate((element) => { element.dataset.committedMap = "kept"; });

  failMapRefresh = true;
  await page.getByRole("button", { name: "再読込" }).click();
  await expect(page.locator("[data-freshness-banner] [data-overview-refresh-error]")).toHaveText(
    "最新データを取得できませんでした。表示中の内容を保持しています。",
  );
  await expect(page.locator("#japanMap")).toHaveAttribute("data-committed-map", "kept");
  await expect(page.locator("#japanMap svg")).toHaveCount(1);
  await expect(page.locator('[data-module="kpis"] .kpiCard').first()).toContainText("24");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

test("a detached chart render exception cannot destroy or replace committed charts", async ({ page }) => {
  await installApiMocks(page);
  await page.goto("/dashboard");
  await expect(page.locator("#usageChart")).toHaveCount(1);
  await expect(page.locator("#usageChart + .chartDataTable")).toContainText("質問数");
  await page.locator("#usageChart").evaluate((element) => { element.dataset.committedChart = "kept"; });
  await page.locator('[data-module="kpis"]').evaluate((element) => { element.dataset.committedBody = "kept"; });
  await page.evaluate(() => {
    window.__originalMonitorChart = window.Chart;
    window.Chart = function BrokenChart() { throw new Error("chart render failed"); };
  });

  await page.getByRole("button", { name: "再読込" }).click();
  await expect(page.locator("[data-freshness-banner] [data-overview-refresh-error]")).toHaveText(
    "最新データを取得できませんでした。表示中の内容を保持しています。",
  );
  await expect(page.locator("#usageChart")).toHaveAttribute("data-committed-chart", "kept");
  await expect(page.locator("#usageChart + .chartDataTable")).toContainText("質問数");
  await expect(page.locator('[data-module="kpis"]')).toHaveAttribute("data-committed-body", "kept");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

test("a DOM commit fault cannot destroy the previously committed Overview charts", async ({ page }) => {
  await installApiMocks(page);
  await page.goto("/dashboard");
  await expect(page.locator("#usageChart")).toHaveCount(1);
  await page.locator("#usageChart").evaluate((element) => { element.dataset.commitFaultChart = "kept"; });
  await page.locator('[data-module="kpis"]').evaluate((element) => { element.dataset.commitFaultBody = "kept"; });
  await page.evaluate(() => {
    const originalDestroy = window.Chart.prototype.destroy;
    window.__committedOverviewDestroyCount = 0;
    window.Chart.prototype.destroy = function trackedDestroy(...args) {
      if (this.canvas?.dataset?.commitFaultChart === "kept") window.__committedOverviewDestroyCount += 1;
      return originalDestroy.apply(this, args);
    };
    const pageRoot = document.querySelector("#pageRoot");
    pageRoot.replaceChildren = () => { throw new Error("overview DOM commit failed"); };
  });

  await page.getByRole("button", { name: "再読込" }).click();

  await expect(page.locator("[data-freshness-banner] [data-overview-refresh-error]")).toHaveText(
    "最新データを取得できませんでした。表示中の内容を保持しています。",
  );
  await expect(page.locator('[data-module="kpis"]')).toHaveAttribute("data-commit-fault-body", "kept");
  await expect(page.locator("#usageChart")).toHaveAttribute("data-commit-fault-chart", "kept");
  expect(await page.evaluate(() => window.__committedOverviewDestroyCount)).toBe(0);
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

test("an initial map staging failure stays local and renders an explicit module error", async ({ page }) => {
  await installApiMocks(page);
  await page.route(/\/dashboard-assets\/assets\/japan-regions\.svg(?:\?.*)?$/, async (route) => route.fulfill({
    status: 503,
    body: "map unavailable",
  }));
  await page.goto("/dashboard");

  await expect(page.locator('[data-module="map"]')).toContainText("日本地図を読み込めませんでした");
  await expect(page.locator('[data-module="kpis"] .kpiCard').first()).toContainText("24");
  await expect(page.locator("#regionRanking")).toContainText("関西");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

test("an initial adapter failure stays inside its module and cannot publish an export snapshot", async ({ page }) => {
  await installApiMocks(page, { overviewOverride: { kpis: null } });
  await page.goto("/dashboard");

  await expect(page.locator('[data-module="kpis"]')).toContainText("主要KPIを表示できません");
  await expect(page.locator('[data-module="usage"]')).toContainText("途中集計");
  await expect(page.locator("#regionRanking")).toContainText("関西");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

test("one analytics API failure stays local and does not turn missing data into zero", async ({ page }) => {
  await installApiMocks(page, { failOverview: true });
  await page.goto("/dashboard");
  await expect(page.locator('[data-module="kpis"]')).toContainText("データを読み込めませんでした");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.locator("#regionRanking")).toContainText("関西");
});

test("a user-list failure cannot erase KPI or region bodies and cannot export stale rows", async ({ page }) => {
  await installApiMocks(page, { failOverviewUsers: true });
  await page.goto("/dashboard");

  await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
  await expect(page.locator("#regionRanking")).toContainText("関西");
  await expect(page.locator('[data-module="users"]')).toContainText("データを読み込めませんでした");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

for (const diagnosticsCase of [
  {
    status: "unavailable",
    issues: ["label_catalog_unavailable"],
    message: "ラベル情報を取得できません。利用状況は表示しています。",
  },
  {
    status: "partial",
    issues: ["unknown_label_reference"],
    message: "一部のラベル情報を除外しました。利用状況は表示しています。",
  },
]) {
  test(`label catalog ${diagnosticsCase.status} keeps usage bodies and disables export`, async ({ page }) => {
    await installApiMocks(page, {
      usersOverride: {
        contentDiagnostics: {
          state: "degraded",
          labelCatalogStatus: diagnosticsCase.status,
          rosterStatus: "available",
          rosterIsolatedCount: 0,
          rosterIssueCounts: {},
          issues: diagnosticsCase.issues,
        },
      },
    });
    await page.goto("/dashboard");

    await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
    await expect(page.locator('[data-module="users"] [data-content-diagnostics]')).toHaveText(diagnosticsCase.message);
    await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
    await expect(page.locator("#regionRanking")).toContainText("関西");
    await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
  });
}

test("partial roster diagnostics keep valid users visible, disclose isolation and disable export", async ({ page }) => {
  await installApiMocks(page, {
    usersOverride: {
      contentDiagnostics: {
        state: "degraded",
        labelCatalogStatus: "available",
        rosterStatus: "partial",
        rosterIsolatedCount: 1,
        rosterIssueCounts: { missing_area_key: 1 },
        issues: ["roster_missing_area_key"],
      },
    },
  });
  await page.goto("/dashboard");

  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.locator('[data-module="users"] [data-content-diagnostics]')).toContainText(
    "名簿データの不備により 1件を除外しました。残りの利用状況は表示しています。",
  );
  await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
  await expect(page.locator("#regionRanking")).toContainText("関西");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

test("Overview roster isolation is disclosed directly without hiding valid KPI bodies", async ({ page }) => {
  await installApiMocks(page, {
    overviewOverride: {
      contentDiagnostics: {
        state: "degraded",
        labelCatalogStatus: "not_applicable",
        rosterStatus: "partial",
        rosterIsolatedCount: 2,
        rosterIssueCounts: { duplicate_email: 2 },
        issues: ["roster_duplicate_email"],
      },
    },
  });
  await page.goto("/dashboard");

  await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
  await expect(page.locator("[data-freshness-banner]")).toContainText(
    "名簿データの不備により 2件を除外しました。残りの利用状況は表示しています。",
  );
  await expect(page.locator("#regionRanking")).toContainText("関西");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

test("Regions roster isolation is disclosed beside the ranking and keeps the map data visible", async ({ page }) => {
  await installApiMocks(page, {
    regionsOverride: {
      contentDiagnostics: {
        state: "degraded",
        labelCatalogStatus: "not_applicable",
        rosterStatus: "partial",
        rosterIsolatedCount: 1,
        rosterIssueCounts: { missing_area_key: 1 },
        issues: ["roster_missing_area_key"],
      },
    },
  });
  await page.goto("/dashboard");

  await expect(page.locator("#regionRanking")).toContainText("関西");
  await expect(page.locator('[data-module="ranking"] [data-region-content-diagnostics]')).toContainText(
    "名簿データの不備により 1件を除外しました。残りの利用状況は表示しています。",
  );
  await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

test("contradictory or rolling-old roster diagnostics keep bodies but fail export closed", async ({ page }) => {
  await installApiMocks(page, {
    overviewOverride: {
      contentDiagnostics: {
        state: "complete",
        labelCatalogStatus: "available",
        rosterStatus: "partial",
        rosterIsolatedCount: 1,
        rosterIssueCounts: { duplicate_email: 1 },
        issues: [],
      },
    },
    regionsOverride: {
      contentDiagnostics: {
        state: "complete",
        labelCatalogStatus: "not_applicable",
        issues: [],
      },
    },
  });
  await page.goto("/dashboard");

  await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
  await expect(page.locator("[data-freshness-banner]")).toContainText("診断情報の整合性を確認できないためCSV出力を停止しています");
  await expect(page.locator("#regionRanking")).toContainText("関西");
  await expect(page.locator('[data-module="ranking"] [data-region-content-diagnostics]')).toContainText("名簿診断情報を確認できません");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

test("an A/A/B mismatch retries three times and commits only the overview-owned A modules", async ({ page }) => {
  const requestCounts = { overview: 0, regions: 0, users: 0 };
  const transactionAnchors = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    const path = url.pathname;
    if (path === "/api/analytics/overview") requestCounts.overview += 1;
    if (path === "/api/analytics/regions") requestCounts.regions += 1;
    if (path === "/api/analytics/overview/users") requestCounts.users += 1;
    if (["/api/analytics/overview", "/api/analytics/regions", "/api/analytics/overview/users"].includes(path)) {
      transactionAnchors.push(url.searchParams.get("as_of"));
    }
  });
  await installApiMocks(page, {
    regionsOverride: {
      rosterFingerprint: "stale-region-roster",
      publishedRunId: "stale-region-run",
    },
  });
  await page.goto("/dashboard");

  await expect(page.locator("[data-freshness-banner]")).toContainText("同じ公開データ版を取得できませんでした");
  await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.locator("#regionRanking")).toHaveCount(0);
  await expect(page.locator('[data-module="ranking"]')).toContainText("同じ公開データ版を取得できませんでした");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
  expect(requestCounts).toEqual({ overview: 3, regions: 3, users: 3 });
  expect(transactionAnchors.every(Boolean)).toBeTruthy();
  expect(new Set(transactionAnchors).size).toBe(1);
});

test("a transient snapshot mismatch converges within the bounded retry and commits one complete version", async ({ page }) => {
  const requestCounts = { overview: 0, regions: 0, users: 0 };
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path === "/api/analytics/overview") requestCounts.overview += 1;
    if (path === "/api/analytics/regions") requestCounts.regions += 1;
    if (path === "/api/analytics/overview/users") requestCounts.users += 1;
  });
  await installApiMocks(page, {
    usersOverride: {
      contentDiagnostics: {
        state: "complete", labelCatalogStatus: "available", rosterStatus: "available",
        rosterIsolatedCount: 0, rosterIssueCounts: {}, issues: [], exportAvailable: true,
      },
    },
  });
  let regionAttempt = 0;
  await page.route(/\/api\/analytics\/regions(?:\?.*)?$/, async (route) => {
    regionAttempt += 1;
    return route.fulfill({
      json: regionAttempt === 1 ? { ...regions, ...snapshotReceipt("transient") } : regions,
    });
  });
  await page.goto("/dashboard");

  await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
  await expect(page.locator("#regionRanking")).toContainText("関西");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.locator("[data-freshness-banner]")).not.toContainText("同じ公開データ版を取得できませんでした");
  await expect(page.getByRole("button", { name: "CSV" })).toBeEnabled();
  expect(requestCounts).toEqual({ overview: 2, regions: 2, users: 2 });
});

test("a content-only A/B mismatch fails the sibling locally and disables CSV", async ({ page }) => {
  await installApiMocks(page, {
    regionsOverride: { contentFingerprint: "content-other-publication" },
  });
  await page.goto("/dashboard");

  await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.locator("#regionRanking")).toHaveCount(0);
  await expect(page.locator('[data-module="ranking"]')).toContainText("同じ公開データ版を取得できませんでした");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

test("a content-only mismatch on refresh preserves the verified Summary body", async ({ page }) => {
  await installApiMocks(page);
  let regionCalls = 0;
  await page.route(/\/api\/analytics\/regions(?:\?.*)?$/, async (route) => {
    regionCalls += 1;
    return route.fulfill({
      json: regionCalls === 1 ? regions : {
        ...regions,
        contentFingerprint: "content-drifted-label-snapshot",
        regions: [{
          areaKey: "不整合", area: "不整合地域", rosterUsers: 1,
          activeUsers: 1, questions: 999, adoptionRate: 1, returnRate: 1,
        }],
      },
    });
  });
  await page.goto("/dashboard");
  await expect(page.locator("#regionRanking")).toContainText("関西");

  await page.getByRole("button", { name: "再読込" }).click();
  await expect(page.locator("[data-freshness-banner]")).toContainText("同じ公開データ版を取得できませんでした");
  await expect(page.locator("#regionRanking")).toContainText("関西");
  await expect(page.locator("#regionRanking")).not.toContainText("不整合地域");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

test("new and legacy receipts select the complete sibling majority without co-committing legacy", async ({ page }) => {
  const requestCounts = { overview: 0, regions: 0, users: 0 };
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path === "/api/analytics/overview") requestCounts.overview += 1;
    if (path === "/api/analytics/regions") requestCounts.regions += 1;
    if (path === "/api/analytics/overview/users") requestCounts.users += 1;
  });
  await installApiMocks(page, { overviewOverride: legacyReceipt });
  await page.goto("/dashboard");

  await expect(page.locator("#kpis .kpiCard")).toHaveCount(0);
  await expect(page.locator('[data-module="kpis"]')).toContainText("同じ公開データ版を取得できませんでした");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.locator("#regionRanking")).toContainText("関西");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
  expect(requestCounts).toEqual({ overview: 3, regions: 3, users: 3 });
});

test("an A/B/C mismatch keeps only the overview owner and rejects both sibling bodies", async ({ page }) => {
  await installApiMocks(page, {
    regionsOverride: snapshotReceipt("B"),
    usersOverride: snapshotReceipt("C"),
  });
  await page.goto("/dashboard");

  await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
  await expect(page.locator("#regionRanking")).toHaveCount(0);
  await expect(page.locator("#overviewUsers")).toHaveCount(0);
  await expect(page.locator('[data-module="ranking"]')).toContainText("同じ公開データ版を取得できませんでした");
  await expect(page.locator('[data-module="users"]')).toContainText("同じ公開データ版を取得できませんでした");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

test("a persistent mismatch on a later refresh preserves the previously committed body", async ({ page }) => {
  await installApiMocks(page);
  let overviewCalls = 0;
  let regionCalls = 0;
  let userCalls = 0;
  await page.route(/\/api\/analytics\/overview(?:\?.*)?$/, async (route) => {
    overviewCalls += 1;
    return route.fulfill({ json: overviewCalls === 1 ? overview : {
      ...overview,
      kpis: { ...overview.kpis, activeUsers: 999 },
    } });
  });
  await page.route(/\/api\/analytics\/regions(?:\?.*)?$/, async (route) => {
    regionCalls += 1;
    return route.fulfill({ json: regionCalls === 1 ? regions : {
      ...regions,
      ...snapshotReceipt("B"),
      regions: [{
        areaKey: "不整合", area: "不整合地域", rosterUsers: 1,
        activeUsers: 1, questions: 999, adoptionRate: 1, returnRate: 1,
      }],
    } });
  });
  await page.route(/\/api\/analytics\/overview\/users(?:\?.*)?$/, async (route) => {
    userCalls += 1;
    return route.fulfill({ json: userCalls === 1 ? overviewUsers : {
      ...overviewUsers,
      ...snapshotReceipt("C"),
      users: [{ ...overviewUsers.users[0], name: "不整合ユーザー" }],
    } });
  });
  await page.goto("/dashboard");
  await expect(page.locator('[data-module="kpis"] .kpiCard').first()).toContainText("24");
  await expect(page.locator("#regionRanking")).toContainText("関西");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");

  await page.locator("#overviewUserSearch").fill("persistent");
  await expect(page.locator("[data-freshness-banner]")).toContainText("同じ公開データ版を取得できませんでした");
  await expect(page.locator('[data-module="kpis"] .kpiCard').first()).toContainText("24");
  await expect(page.locator('[data-module="kpis"]')).not.toContainText("999");
  await expect(page.locator("#regionRanking")).toContainText("関西");
  await expect(page.locator("#regionRanking")).not.toContainText("不整合地域");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.locator("#overviewUsers")).not.toContainText("不整合ユーザー");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
  expect({ overviewCalls, regionCalls, userCalls }).toEqual({ overviewCalls: 4, regionCalls: 4, userCalls: 5 });
});

test("a complete-to-legacy user refresh transition runs the full transaction and preserves committed rows", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, {
    requests,
    overviewUsersByQuery: {
      "new-to-legacy": {
        ...legacyReceipt,
        users: [{ ...overviewUsers.users[0], name: "混在した旧形式ユーザー" }],
      },
    },
  });
  await page.goto("/dashboard");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.getByRole("button", { name: "CSV" })).toBeEnabled();

  await page.locator("#overviewUserSearch").fill("new-to-legacy");
  await expect(page.locator("[data-freshness-banner]")).toContainText("同じ公開データ版を取得できませんでした");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.locator("#overviewUsers")).not.toContainText("混在した旧形式ユーザー");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
  expect(requests.filter((row) => row.path === "/api/analytics/overview")).toHaveLength(4);
  expect(requests.filter((row) => row.path === "/api/analytics/regions")).toHaveLength(4);
  expect(requests.filter((row) => row.path === "/api/analytics/overview/users")).toHaveLength(5);
});

test("a slow full transaction started by search A cannot overwrite newer search B", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, { requests });
  let slowFullStarted = false;
  let pendingSlowResponses = 0;
  let completedSlowResponses = 0;
  let releaseSlowResponses;
  const slowGate = new Promise((resolve) => { releaseSlowResponses = resolve; });
  await page.route(/\/api\/analytics\/overview(?:\?.*)?$/, async (route) => {
    if (!slowFullStarted) return route.fallback();
    pendingSlowResponses += 1;
    await slowGate;
    await route.fulfill({
      json: { ...overview, kpis: { ...overview.kpis, activeUsers: 999 } },
    });
    completedSlowResponses += 1;
  });
  await page.route(/\/api\/analytics\/regions(?:\?.*)?$/, async (route) => {
    if (!slowFullStarted) return route.fallback();
    pendingSlowResponses += 1;
    await slowGate;
    await route.fulfill({
      json: {
        ...regions,
        regions: [{
          areaKey: "slow-a", area: "遅いA地域", rosterUsers: 1,
          activeUsers: 1, questions: 999, adoptionRate: 1, returnRate: 1,
        }],
      },
    });
    completedSlowResponses += 1;
  });
  let slowAUserCalls = 0;
  await page.route(/\/api\/analytics\/overview\/users(?:\?.*)?$/, async (route) => {
    const query = new URL(route.request().url()).searchParams.get("q") || "";
    if (query === "slow-a") {
      slowAUserCalls += 1;
      if (slowAUserCalls === 1) {
        slowFullStarted = true;
        return route.fulfill({
          json: {
            ...overviewUsers,
            ...legacyReceipt,
            users: [{ ...overviewUsers.users[0], name: "旧形式Aユーザー" }],
          },
        });
      }
      pendingSlowResponses += 1;
      await slowGate;
      await route.fulfill({
        json: {
          ...overviewUsers,
          users: [{ ...overviewUsers.users[0], name: "遅いAユーザー" }],
        },
      });
      completedSlowResponses += 1;
      return undefined;
    }
    if (query === "fast-b") {
      return route.fulfill({
        json: {
          ...overviewUsers,
          users: [{ ...overviewUsers.users[0], name: "高速Bユーザー" }],
        },
      });
    }
    return route.fallback();
  });

  await page.goto("/dashboard");
  await page.locator("#japanMap").evaluate((element) => { element.dataset.raceCommittedMap = "kept"; });
  await page.locator("#overviewUserSearch").fill("slow-a");
  await expect.poll(() => pendingSlowResponses).toBe(3);

  await page.locator("#overviewUserSearch").fill("fast-b");
  await expect(page.locator("#overviewUsers")).toContainText("高速Bユーザー");
  await expect(page.getByRole("button", { name: "CSV" })).toBeEnabled();

  releaseSlowResponses();
  await expect.poll(() => completedSlowResponses).toBe(3);
  await expect(page.locator('[data-module="kpis"] .kpiCard').first()).toContainText("24");
  await expect(page.locator('[data-module="kpis"]')).not.toContainText("999");
  await expect(page.locator("#regionRanking")).toContainText("関西");
  await expect(page.locator("#regionRanking")).not.toContainText("遅いA地域");
  await expect(page.locator("#japanMap")).toHaveAttribute("data-race-committed-map", "kept");
  await expect(page.locator("#japanMap svg")).toHaveCount(1);
  await expect(page.locator("#overviewUsers")).toContainText("高速Bユーザー");
  await expect(page.locator("#overviewUsers")).not.toContainText("遅いAユーザー");
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "CSV" }).click();
  await downloadPromise;
  await expect(page.locator("#toast")).toContainText("CSVをダウンロードしました");
  expect(requests.filter((row) => (
    row.method === "POST" && row.path === "/api/export/jobs"
  )).at(-1)?.body).toMatchObject({
    q: "fast-b",
    expectedPublishedRunId: "run-20260823-01",
    expectedRosterFingerprint: "roster-fingerprint-1",
    expectedContentFingerprint: "content-fingerprint-1",
    expectedScopePolicyVersion: "summary_role_v1",
    expectedWindowStart: "2026-08-16T15:00:00Z",
    expectedWindowEnd: "2026-08-23T01:00:00Z",
    expectedWindowTimezone: "Asia/Tokyo",
  });
});

test("an all-legacy Overview preserves usable bodies but fails CSV closed", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, {
    requests,
    overviewOverride: legacyReceipt,
    regionsOverride: legacyReceipt,
    usersOverride: legacyReceipt,
  });
  await page.goto("/dashboard");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.locator("#regionRanking")).toContainText("関西");
  await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
  await expect(page.locator("[data-freshness-banner]")).toContainText("旧形式");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
  expect(requests.filter((row) => row.path === "/api/analytics/overview")).toHaveLength(1);
  expect(requests.filter((row) => row.path === "/api/analytics/regions")).toHaveLength(1);
  expect(requests.filter((row) => row.path === "/api/analytics/overview/users")).toHaveLength(1);
});

test("an all-legacy refresh cannot overwrite a previously verified Overview snapshot", async ({ page }) => {
  await installApiMocks(page);
  let serveLegacy = false;
  await page.route(/\/api\/analytics\/overview(?:\?.*)?$/, async (route) => {
    if (!serveLegacy) return route.fallback();
    return route.fulfill({
      json: {
        ...overview,
        ...legacyReceipt,
        kpis: { ...overview.kpis, activeUsers: 999 },
      },
    });
  });
  await page.route(/\/api\/analytics\/regions(?:\?.*)?$/, async (route) => {
    if (!serveLegacy) return route.fallback();
    return route.fulfill({
      json: {
        ...regions,
        ...legacyReceipt,
        regions: [{
          areaKey: "旧形式", area: "旧形式地域", rosterUsers: 1,
          activeUsers: 1, questions: 999, adoptionRate: 1, returnRate: 1,
        }],
      },
    });
  });
  await page.route(/\/api\/analytics\/overview\/users(?:\?.*)?$/, async (route) => {
    if (!serveLegacy) return route.fallback();
    return route.fulfill({
      json: {
        ...overviewUsers,
        ...legacyReceipt,
        users: [{ ...overviewUsers.users[0], name: "旧形式ユーザー" }],
      },
    });
  });

  await page.goto("/dashboard");
  await expect(page.locator('[data-module="kpis"] .kpiCard').first()).toContainText("24");
  await expect(page.locator("#regionRanking")).toContainText("関西");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.getByRole("button", { name: "CSV" })).toBeEnabled();

  serveLegacy = true;
  await page.getByRole("button", { name: "再読込" }).click();
  await expect(page.locator("[data-freshness-banner]")).toContainText("同じ公開データ版を取得できませんでした");
  await expect(page.locator('[data-module="kpis"] .kpiCard').first()).toContainText("24");
  await expect(page.locator('[data-module="kpis"]')).not.toContainText("999");
  await expect(page.locator("#regionRanking")).toContainText("関西");
  await expect(page.locator("#regionRanking")).not.toContainText("旧形式地域");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.locator("#overviewUsers")).not.toContainText("旧形式ユーザー");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

test("a refresh failure cannot erase initially rendered legacy bodies", async ({ page }) => {
  await installApiMocks(page, {
    overviewOverride: legacyReceipt,
    regionsOverride: legacyReceipt,
    usersOverride: legacyReceipt,
  });
  let failRefresh = false;
  await page.route(/\/api\/analytics\/(?:overview(?:\/users)?|regions)(?:\?.*)?$/, async (route) => {
    if (!failRefresh) return route.fallback();
    return route.fulfill({
      status: 503,
      json: { detail: { code: "source_unavailable", message: "legacy refresh unavailable" } },
    });
  });

  await page.goto("/dashboard");
  await expect(page.locator('[data-module="kpis"] .kpiCard').first()).toContainText("24");
  await expect(page.locator("#regionRanking")).toContainText("関西");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.locator("[data-freshness-banner]")).toContainText("旧形式");

  failRefresh = true;
  await page.getByRole("button", { name: "再読込" }).click();
  await expect(page.locator("[data-freshness-banner]")).toContainText(
    "最新データを取得できませんでした。表示中の内容を保持しています。",
  );
  await expect(page.locator('[data-module="kpis"] .kpiCard').first()).toContainText("24");
  await expect(page.locator("#regionRanking")).toContainText("関西");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

test("a failed filtered-user refresh keeps committed rows with one stable inline error", async ({ page }) => {
  await installApiMocks(page);
  await page.route(/\/api\/analytics\/overview\/users(?:\?.*)?$/, async (route) => {
    const query = new URL(route.request().url()).searchParams.get("q") || "";
    if (query !== "refresh-failure") return route.fallback();
    return route.fulfill({
      status: 503,
      json: { detail: { code: "source_unavailable", message: "filtered users unavailable" } },
    });
  });
  await page.goto("/dashboard");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.getByRole("button", { name: "CSV" })).toBeEnabled();

  await page.locator("#overviewUserSearch").fill("refresh-failure");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.locator('[data-module="users"] [data-user-refresh-error]')).toHaveText(
    "ユーザー一覧を更新できませんでした。表示中の内容を保持しています。",
  );
  await expect(page.locator('[data-module="users"] [data-user-refresh-error]')).toHaveCount(1);
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

test("historical environment gaps are explained without fake unknown charts", async ({ page }) => {
  await installApiMocks(page, {
    overviewOverride: {
      deviceDistribution: [],
      deviceMeasurement: { measuredCount: 0, totalCount: 77, measurementState: "not_measured", measurementReason: "historical_unavailable" },
      modeDistribution: [],
      modeMeasurement: { measuredCount: 0, totalCount: 77, measurementState: "not_measured", measurementReason: "historical_unavailable" },
    },
  });
  await page.goto("/dashboard");
  const environment = page.locator('[data-module="environment"]');
  await expect(environment.getByText("過去データにはこの項目が保存されていません")).toHaveCount(2);
  await expect(environment.locator("#deviceChart")).toHaveCount(0);
  await expect(environment.locator("#modeChart")).toHaveCount(0);
  await expect(page.locator('[data-module="kpis"] .kpiCard')).toHaveCount(6);
});

test("one broken environment axis cannot erase valid hourly and device analytics", async ({ page }) => {
  await installApiMocks(page, {
    overviewOverride: {
      modeMeasurement: { measuredCount: 78, totalCount: 77, measurementState: "measured", measurementReason: "complete" },
    },
  });
  await page.goto("/dashboard");

  const environment = page.locator('[data-module="environment"]');
  await expect(environment.locator("#hourChart")).toHaveCount(1);
  await expect(environment.locator("#deviceChart")).toHaveCount(1);
  await expect(environment).toContainText("モード分析の計測範囲");
  await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
});

test("one malformed product or trend row is isolated without erasing valid rows", async ({ page }) => {
  await installApiMocks(page, {
    overviewOverride: {
      usageTrend: [
        overview.usageTrend[0],
        { date: "2026-08-23", activeUsers: -1, questions: 62, isPartial: true },
      ],
      productTaskMatrix: [
        overview.productTaskMatrix[0],
        { product: "破損行", task: "fact_lookup", taskLabel: "情報確認", count: -1 },
      ],
    },
  });
  await page.goto("/dashboard");

  await expect(page.locator('[data-module="usage"] #usageChart')).toHaveCount(1);
  await expect(page.locator('[data-module="usage"]')).toContainText("2行目");
  await expect(page.locator('[data-module="products"] #productChart')).toHaveCount(1);
  await expect(page.locator('[data-module="products"] #productMatrix')).toContainText("テルフュージョン");
  await expect(page.locator('[data-module="products"]')).toContainText("製品マトリクス 2行目");
});

test("unresolved product candidates are disclosed beside product analytics", async ({ page }) => {
  await installApiMocks(page, {
    overviewOverride: {
      productResolution: {
        candidateCount: 12,
        resolvedCount: 10,
        unresolvedQuestions: 2,
        resolutionRate: 10 / 12,
        measuredCount: 12,
        totalCount: 12,
        measurementState: "measured",
        measurementReason: "complete",
      },
    },
  });
  await page.goto("/dashboard");
  await expect(page.locator('[data-module="products"]')).toContainText(
    "正式な製品名を確認できなかった質問 2件",
  );
});

test("an unknown historical category is shown as unclassified without hiding valid modules", async ({ page }) => {
  await installApiMocks(page, {
    overviewOverride: {
      questionCategories: [{ key: "unclassified", label: "判定不能", count: 1, rate: 1 }],
    },
  });
  await page.goto("/dashboard");
  await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
  await expect(page.locator("main")).toContainText("判定不能");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
});

test("one user with missing analytics does not hide other valid users", async ({ page }) => {
  await installApiMocks(page, {
    usersOverride: {
      users: [{
        rosterId: "roster_ok",
        name: "正常ユーザー",
        email: "ok@example.com",
        role: "本社MR",
        department: "DM専任",
        workplace: "大阪",
        area: "関西",
        areaKey: "関西",
        labels: [],
        lastActiveAt: "",
        activeDays7: 0,
        userMessageCount7: 0,
        completeDelivery: { value: null, measuredCount: 0, totalCount: 0, measurementState: "no_usage", measurementReason: "no_usage" },
        activity: "dormant",
        activityLabel: "休眠ユーザー",
      }, {
        rosterId: "roster_bad",
        name: "契約欠落",
        email: "missing@example.com",
        role: "コントラクトMR",
        department: "DM専任",
        workplace: "大阪",
        area: "関西",
        areaKey: "関西",
        labels: [],
        lastActiveAt: "",
        activeDays7: 0,
        userMessageCount7: 0,
        completeDelivery: { value: null, measuredCount: 0, totalCount: 0, measurementState: "no_usage", measurementReason: "no_usage" },
        activityLabel: "休眠ユーザー",
      }],
    },
  });
  await page.goto("/dashboard");
  await expect(page.locator("#overviewUsers")).toContainText("正常ユーザー");
  await expect(page.locator("#overviewUsers")).toContainText("契約欠落");
  await expect(page.locator("#overviewUsers")).toContainText("未測定");
  await expect(page.locator('[data-module="users"]')).toContainText("契約上の欠落を 1件");
  await expect(page.locator("#regionRanking")).toContainText("関西");
});

test("stale freshness metadata never hides otherwise available data", async ({ page }) => {
  const staleFreshness = {
    ...overview.freshness,
    state: "stale",
    dataThrough: "2026-08-20T00:00:00Z",
  };
  await installApiMocks(page, {
    overviewOverride: { freshness: staleFreshness },
    usersOverride: { freshness: staleFreshness },
  });
  await page.goto("/dashboard");
  await expect(page.locator("[data-freshness-banner]")).not.toContainText("3時間ごと");
  await expect(page.locator("[data-freshness-banner]")).toContainText("反映済み");
  await expect(page.locator("[data-freshness-banner]")).toContainText("更新が遅れています");
  await expect(page.locator("[data-freshness-banner]")).toContainText("元イベント 2件");
  await expect(page.locator("[data-freshness-banner]")).toContainText("重複配信 3件");
  await expect(page.locator("[data-freshness-banner]")).toContainText("重複ファクト 1件");
  await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.locator("#regionRanking")).toContainText("関西");
});

test("malformed update metadata is isolated and never erases valid overview modules", async ({ page }) => {
  await installApiMocks(page, {
    overviewOverride: {
      scopeUserCount: null,
      freshness: { state: "broken" },
      analyticsQuality: { contractVersion: "broken" },
    },
  });
  await page.goto("/dashboard");

  await expect(page.locator("[data-freshness-banner]")).toContainText("更新情報を確認できません");
  await expect(page.locator("[data-freshness-banner]")).toContainText("表示中の集計値は保持しています");
  await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
  await expect(page.locator('[data-module="usage"]')).toContainText("途中集計");
  await expect(page.locator('[data-module="products"]')).toContainText("テルフュージョン");
});

test("rolling old response fields keep valid bodies visible but never enable an unverifiable CSV", async ({ page }) => {
  const legacyComplete = { ...overview.kpis.completeDelivery };
  delete legacyComplete.measurementReason;
  const legacyKpis = { ...overview.kpis, completeDelivery: legacyComplete };
  delete legacyKpis.p95Latency;
  await installApiMocks(page, {
    overviewOverride: {
      scopePolicyVersion: null,
      rosterFingerprint: null,
      publishedRunId: null,
      kpis: legacyKpis,
    },
    usersOverride: {
      scopePolicyVersion: null,
      rosterFingerprint: null,
      publishedRunId: null,
    },
    regionsOverride: {
      scopePolicyVersion: null,
      rosterFingerprint: null,
      publishedRunId: null,
    },
  });
  await page.goto("/dashboard");

  await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
  await expect(page.locator('[data-module="kpis"]')).toContainText("回答成功率");
  await expect(page.locator('[data-module="kpis"]')).toContainText("P95応答時間: 計測範囲を確認できません");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.locator("#regionRanking")).toContainText("関西");
  await expect(page.locator("[data-freshness-banner]")).toContainText("旧形式");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

test("an invalid analysis window cannot be co-committed with complete summary receipts", async ({ page }) => {
  await installApiMocks(page, {
    usersOverride: { windowStart: "not-a-date" },
  });
  await page.goto("/dashboard");

  await expect(page.locator('[data-module="kpis"]')).toContainText("24");
  await expect(page.locator("#overviewUsers")).toHaveCount(0);
  await expect(page.locator('[data-module="users"]')).toContainText("同じ公開データ版を取得できませんでした");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

test("one broken KPI field cannot erase valid sibling KPI cards", async ({ page }) => {
  await installApiMocks(page, {
    overviewOverride: {
      kpis: {
        ...overview.kpis,
        p95Latency: { valueMs: 5000, measuredCount: 8, totalCount: 7, measurementState: "measured", measurementReason: "complete" },
      },
    },
  });
  await page.goto("/dashboard");

  await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
  await expect(page.locator('[data-module="kpis"]')).toContainText("91.0%");
  await expect(page.locator('[data-module="kpis"]')).toContainText("計測情報なし");
  await expect(page.locator('[data-module="usage"]')).toContainText("途中集計");
});

test("unavailable pipeline diagnostics are explicit while published facts remain visible", async ({ page }) => {
  const analyticsQuality = {
    ...overview.analyticsQuality,
    sourcePipeline: {
      publishedRunId: "run-20260823-01",
      latestRunId: "",
      latestRunStatus: "",
      latestRunErrorCode: "",
      latestRunFinishedAt: "",
      diagnosticsStatus: "unavailable",
      diagnosticsErrorCode: "schema_unavailable",
      state: "unavailable",
      quarantinedEventCount: 0,
      deduplicatedDeliveryCount: 0,
      repairedDuplicateFactCount: 0,
      axisUnmeasuredFindingCount: 0,
      batchBlockingFailureCount: 0,
    },
  };
  await installApiMocks(page, { overviewOverride: { analyticsQuality } });
  await page.goto("/dashboard");

  await expect(page.locator("[data-freshness-banner]")).toContainText("診断情報を確認できません");
  await expect(page.locator("[data-freshness-banner]")).toContainText("表示中の集計値は保持しています");
  await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
});

test("rolling compatibility and independent region-user metadata failures preserve each body", async ({ page }) => {
  const legacySourcePipeline = { ...overview.analyticsQuality.sourcePipeline };
  delete legacySourcePipeline.diagnosticsStatus;
  delete legacySourcePipeline.diagnosticsErrorCode;
  await installApiMocks(page, {
    overviewOverride: {
      analyticsQuality: {
        ...overview.analyticsQuality,
        sourcePipeline: legacySourcePipeline,
      },
    },
    usersOverride: { scopeUserCount: null, freshness: null },
    regionsOverride: { scopeUserCount: null, freshness: null },
  });
  await page.goto("/dashboard");

  await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.locator("#regionRanking")).toContainText("関西");
  await expect(page.locator("[data-freshness-banner]")).toContainText("反映済み");
  await expect(page.locator("[data-freshness-banner]")).not.toContainText("3時間ごと");
});

test("a blocked latest refresh keeps the previous published dashboard and explains the failure", async ({ page }) => {
  const analyticsQuality = {
    ...overview.analyticsQuality,
    sourcePipeline: {
      ...overview.analyticsQuality.sourcePipeline,
      latestRunId: "run-blocked",
      latestRunStatus: "failed",
      latestRunErrorCode: "DataQualityGateError",
      latestRunFinishedAt: "2026-08-23T02:00:00Z",
      state: "blocked",
      batchBlockingFailureCount: 2,
    },
  };
  await installApiMocks(page, { overviewOverride: { analyticsQuality } });
  await page.goto("/dashboard");

  await expect(page.locator("[data-freshness-banner]")).toContainText("品質チェック 2件");
  await expect(page.locator("[data-freshness-banner]")).toContainText("直前の成功データ");
  await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
});

test("a slower obsolete period request cannot overwrite the latest selection", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, {
    requests,
    overviewDelayByPreset: { last_30d: 700 },
    overviewByPreset: {
      last_30d: { kpis: { ...overview.kpis, activeUsers: 30 } },
      last_14d: { kpis: { ...overview.kpis, activeUsers: 14 } },
    },
  });
  await page.goto("/dashboard");
  await page.locator("#analysisPreset").selectOption("last_30d");
  await expect.poll(() => requests.some((row) => row.path === "/api/analytics/overview" && row.search.includes("last_30d"))).toBeTruthy();
  await page.locator("#analysisPreset").selectOption("last_14d");
  await expect(page.locator('[data-module="kpis"] .kpiCard').first()).toContainText("14");
  await page.waitForTimeout(800);
  await expect(page.locator('[data-module="kpis"] .kpiCard').first()).toContainText("14");
});

test("the real 80-person overview is paginated and never rendered as one long table", async ({ page }) => {
  await installApiMocks(page, { usersOverride: { users: makeAnalyticsUsers(80) } });
  await page.goto("/dashboard");
  await expect(page.locator("#overviewUsers tbody tr")).toHaveCount(15);
  await expect(page.locator('[data-module="users"]')).toContainText("1–15 / 80名");
  await page.locator("#overviewSort").selectOption("name_asc");
  await page.getByRole("button", { name: "次のページ" }).click();
  await expect(page.locator("#overviewUsers tbody tr").first()).toContainText("利用者 16");
});

test("local pagination cannot cancel an in-flight user filter refresh", async ({ page }) => {
  const filteredUsers = makeAnalyticsUsers(80).map((user, index) => ({
    ...user,
    name: `検索結果 ${String(index + 1).padStart(2, "0")}`,
  }));
  const requests = [];
  await installApiMocks(page, {
    requests,
    usersOverride: { users: makeAnalyticsUsers(80) },
    overviewUsersDelayByQuery: { delayed: 700 },
    overviewUsersByQuery: { delayed: { users: filteredUsers } },
  });
  await page.goto("/dashboard");
  await expect(page.locator('[data-module="users"]')).toContainText("1–15 / 80名");

  const filteredRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return url.pathname === "/api/analytics/overview/users"
      && url.searchParams.get("q") === "delayed";
  });
  await page.locator("#overviewUserSearch").fill("delayed");
  await filteredRequest;
  await page.getByRole("button", { name: "次のページ" }).click();

  await expect(page.locator('[data-module="users"]')).toContainText("16–30 / 80名");
  await expect(page.locator("#overviewUsers tbody tr").first()).toContainText("検索結果 16");
  await expect(page.getByRole("button", { name: "CSV" })).toBeEnabled();
  expect(new URLSearchParams(
    requests.filter((row) => row.path === "/api/analytics/overview/users").at(-1).search,
  ).get("q")).toBe("delayed");
});

test("historical not-measured completion is not presented as a global zero", async ({ page }) => {
  await installApiMocks(page, {
    overviewOverride: {
      kpis: {
        ...overview.kpis,
        completeDelivery: measurementForTest(null, 0, 77, "not_measured"),
      },
    },
  });
  await page.goto("/dashboard");
  const card = page.locator('[data-module="kpis"] .kpiCard').filter({ hasText: "回答成功率" });
  await expect(card).toContainText("過去データ未記録");
  await expect(card).not.toContainText("0.0%");
});

function measurementForTest(value, measuredCount, totalCount, measurementState) {
  const measurementReason = measurementState === "measured" ? "complete"
    : measurementState === "no_usage" ? "no_usage" : "historical_unavailable";
  return { value, measuredCount, totalCount, measurementState, measurementReason };
}
