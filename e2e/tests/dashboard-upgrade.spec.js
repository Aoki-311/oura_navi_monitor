const { test, expect } = require("@playwright/test");
const { installApiMocks, overview } = require("./fixtures");
const { openPeriod, draftPeriod, applyPreset } = require("./date-range-helpers");

function newsPayload() {
  return {
    contractVersion: "news_usage_dashboard_v1", scope: "global", rosterId: "",
    windowStart: "2026-09-01T15:00:00Z", windowEnd: "2026-09-06T15:00:00Z",
    publishedRunId: "news-1", rosterFingerprint: "roster-1", state: { availability: "available" },
    totals: { tabViews: 7, newsTabViews: 4, societyTabViews: 3, contentClicks: 12, newsContentClicks: 8, societyContentClicks: 4, newsDomesticClicks: 5, newsOverseasClicks: 2, newsUnknownGeographyClicks: 1 },
    trend: [{ date: "2026-09-05", tabViews: 7, newsTabViews: 4, societyTabViews: 3, contentClicks: 12, newsContentClicks: 8, societyContentClicks: 4 }],
    newsCategories: [{ key: "regulatory_safety", label: "規制・安全", clicks: 8, domesticClicks: 5, overseasClicks: 2, unknownGeographyClicks: 1 }],
    societyCategories: [{ key: "糖尿病関連", label: "糖尿病関連", clicks: 4, sources: [{ key: "jds", label: "日本糖尿病学会", clicks: 3 }, { key: "jaden", label: "日本糖尿病教育・看護学会", clicks: 1 }] }],
  };
}

async function setup(page, requests = []) {
  await installApiMocks(page, { requests });
  await page.route(/\/api\/news-usage\/overview(?:\?.*)?$/, (route) => route.fulfill({ json: newsPayload() }));
}

test("overview integrates four news charts with measured totals and useful hover breakdowns", async ({ page }, testInfo) => {
  await setup(page);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/dashboard");
  await expect(page.locator('[data-module="newsTrend"] canvas')).toHaveCount(1);
  await expect(page.locator('#kpis .kpiCard')).toHaveCount(6);
  await expect(page.locator('.newsUsageDashboard > .panel')).toHaveCount(4);
  const positions = await page.locator('[data-module="products"], .newsUsageDashboard').evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().top));
  expect(positions[1]).toBeGreaterThan(positions[0]);
  await expect(page.locator('.mainNav [data-page="newsusage"]')).toHaveCount(0);
  await expect(page.locator('main')).not.toContainText("公開済み");
  await expect(page.locator('main')).not.toContainText("途中集計");
  await expect(page.locator('main .measurementBadge')).toHaveCount(0);
  const charts = await page.evaluate(() => {
    const get = (name) => window.Chart.getChart(document.querySelector(`[data-module="${name}"] canvas`));
    const trend = get("newsTrend");
    return {
      trend: trend.data.datasets.map((row) => ({ type: row.type, data: [...row.data] })),
      visits: trend.options.plugins.tooltip.callbacks.afterLabel({ dataIndex: 0, datasetIndex: 0 }),
      clicks: trend.options.plugins.tooltip.callbacks.afterLabel({ dataIndex: 0, datasetIndex: 1 }),
      pie: [...get("newsShare").data.datasets[0].data],
      news: get("newsCategories").options.plugins.tooltip.callbacks.afterLabel({ dataIndex: 0 }),
      society: get("societyCategories").options.plugins.tooltip.callbacks.afterLabel({ dataIndex: 0 }),
    };
  });
  expect(charts.trend).toEqual([{ type: "bar", data: [7] }, { type: "line", data: [12] }]);
  expect(charts.pie).toEqual([8, 4]);
  expect(charts.visits).toEqual(["ニュース 4回", "学会 3回"]);
  expect(charts.clicks).toEqual(["ニュース 8回", "学会 4回"]);
  expect(charts.news).toEqual(["国内 5回", "海外 2回", "未分類 1回"]);
  expect(charts.society).toContain("日本糖尿病学会 3回");
  const newsCanvas = page.locator('[data-module="newsCategories"] canvas');
  await newsCanvas.scrollIntoViewIfNeeded();
  const point = await newsCanvas.evaluate((canvas) => {
    const center = window.Chart.getChart(canvas).getDatasetMeta(0).data[0].getCenterPoint();
    const box = canvas.getBoundingClientRect();
    return { x: box.x + center.x, y: box.y + center.y };
  });
  await page.mouse.move(point.x, point.y);
  await expect.poll(() => newsCanvas.evaluate((canvas) => window.Chart.getChart(canvas).tooltip.body?.flatMap((row) => [...row.lines, ...row.after]).join(" "))).toContain("国内 5回");
  await page.getByLabel("活性度の定義").click();
  await expect(page.locator('.activityHelpContent')).toContainText("6日以上");
  await expect(page.locator('.activityHelpContent')).toContainText("3〜5日");
  await expect(page.locator('.activityHelpContent')).toContainText("1〜2日");
  const helpBounds = await page.locator('.activityHelpContent').boundingBox();
  expect(helpBounds.x).toBeGreaterThanOrEqual(0);
  await page.getByLabel("活性度の定義").click();
  await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo(0, 0); });
  expect(errors).toEqual([]);
  if (process.env.MONITOR_E2E_CAPTURE_ARTIFACTS === "1") {
    await page.evaluate(() => {
      for (const canvas of document.querySelectorAll("canvas")) {
        const chart = window.Chart.getChart(canvas);
        chart?.stop();
        chart?.update("none");
      }
    });
    await page.screenshot({ path: testInfo.outputPath("upgraded-overview.png"), fullPage: true });
  }
});

test("main custom period applies separately and survives reread and browser reload", async ({ page }) => {
  const requests = [];
  await setup(page, requests);
  await page.goto("/dashboard");
  await expect(page.locator('#kpis .kpiCard')).toHaveCount(6);
  const main = page.locator('#mainPeriod');
  const before = requests.filter((item) => item.path === "/api/analytics/overview").length;
  await draftPeriod(main, "2026-08-01", "2026-08-31");
  expect(requests.filter((item) => item.path === "/api/analytics/overview")).toHaveLength(before);
  await main.getByRole("button", { name: "反映", exact: true }).click();
  await expect(page).toHaveURL(/start=2026-08-01/);
  await expect(page).toHaveURL(/end=2026-08-31/);
  await expect(page.locator('#mainPeriod [data-applied-range]')).toHaveText("2026/08/01 — 2026/08/31");
  await page.locator('#mainPeriod [data-range-refresh]').click();
  await expect(page.locator('#mainPeriod [data-applied-range]')).toHaveText("2026/08/01 — 2026/08/31");
  await page.reload();
  await expect(page.locator('#mainPeriod [data-applied-range]')).toHaveText("2026/08/01 — 2026/08/31");
  await expect(page.locator('#kpis .kpiCard')).toHaveCount(6);
  const last = requests.filter((item) => item.path === "/api/analytics/overview").at(-1);
  const params = new URLSearchParams(last.search);
  expect(params.get("start")).toBe("2026-08-01T00:00:00+09:00");
  expect(params.get("end")).toBe("2026-09-01T00:00:00+09:00");
  await expect(page.locator('#overviewUsers thead')).toContainText("期間内利用日数");
});

test("environment and usage date applies refresh only their own modules", async ({ page }) => {
  const requests = [];
  await setup(page, requests);
  await page.goto("/dashboard");
  await expect(page.locator('#deviceChart')).toHaveCount(1);
  await expect(page.locator('#usageChart')).toHaveCount(1);
  const before = requests.length;
  const environment = page.locator('[data-module-date="environment"]');
  await openPeriod(environment);
  await environment.getByRole("button", { name: "過去14日", exact: true }).click();
  expect(requests.length).toBe(before);
  await environment.getByRole("button", { name: "反映", exact: true }).click();
  await expect(page).toHaveURL(/env_preset=last_14d/);
  expect(requests.slice(before).map((item) => item.path)).toEqual(["/api/analytics/environment"]);
  const second = requests.length;
  const usage = page.locator('[data-module-date="usage"]');
  await openPeriod(usage);
  await usage.getByRole("button", { name: "過去30日", exact: true }).click();
  await usage.getByRole("button", { name: "反映", exact: true }).click();
  await expect(page).toHaveURL(/trend_preset=last_30d/);
  expect(requests.slice(second).map((item) => item.path)).toEqual(["/api/analytics/trend"]);
  await page.reload();
  await expect(page.locator('[data-module-date="environment"] [data-range-preset="last_14d"]')).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator('[data-module-date="usage"] [data-range-preset="last_30d"]')).toHaveAttribute("aria-pressed", "true");
});

test("module refresh retains its chart while loading and shows a persistent failure", async ({ page }) => {
  await setup(page);
  let fail = false;
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  await page.route(/\/api\/analytics\/environment(?:\?.*)?$/, async (route) => {
    if (!fail) return route.fallback();
    await gate;
    return route.fulfill({ status: 503, json: { detail: "unavailable" } });
  });
  await page.goto("/dashboard");
  await expect(page.locator('#deviceChart')).toHaveCount(1);
  await page.locator('#deviceChart').evaluate((node) => { node.dataset.beforeRefresh = "kept"; });
  fail = true;
  await page.locator('[data-module-date="environment"] [data-range-refresh]').click();
  await expect(page.locator('[data-module="environment"]')).toHaveAttribute("aria-busy", "true");
  await expect(page.locator('#deviceChart')).toHaveAttribute("data-before-refresh", "kept");
  release();
  await expect(page.locator('[data-module="environment"]')).toHaveAttribute("aria-busy", "false");
  await expect(page.locator('[data-module="environment"] [role="alert"]')).toContainText("表示中の内容を保持しています");
  await expect(page.locator('#deviceChart')).toHaveCount(1);
});

test("a slow module response cannot replace the latest applied dates and chart", async ({ page }) => {
  await setup(page);
  let release;
  let slowStarted = false;
  const gate = new Promise((resolve) => { release = resolve; });
  await page.route(/\/api\/analytics\/trend(?:\?.*)?$/, async (route) => {
    const preset = new URL(route.request().url()).searchParams.get("preset");
    if (preset === "last_14d") { slowStarted = true; await gate; }
    const raw = JSON.parse(JSON.stringify(overview));
    raw.usageTrend = [{ ...raw.usageTrend[0], questions: preset === "last_30d" ? 99 : 3 }];
    await route.fulfill({ json: raw }).catch(() => {});
  });
  await page.goto("/dashboard");
  await expect(page.locator('#usageChart')).toHaveCount(1);
  const usage = page.locator('[data-module-date="usage"]');
  await applyPreset(usage, "last_14d");
  await expect.poll(() => slowStarted).toBe(true);
  await applyPreset(usage, "last_30d");
  await expect(page).toHaveURL(/trend_preset=last_30d/);
  release();
  await expect.poll(() => page.locator('#usageChart').evaluate((node) => window.Chart.getChart(node).data.datasets[0].data[0])).toBe(99);
  await expect(page.locator('[data-module-date="usage"] [data-range-preset="last_30d"]')).toHaveAttribute("aria-pressed", "true");
});

test("news read failures and unstarted measurement never become zero or perpetual loading", async ({ page }) => {
  await installApiMocks(page, { failNewsUsage: true });
  await page.goto("/dashboard");
  await expect(page.locator('[data-module="newsTrend"] [role="alert"]')).toContainText("データを取得できませんでした");
  await expect(page.locator('[data-module="newsTrend"] [data-state="loading"]')).toHaveCount(0);
  await expect(page.locator('#kpis .kpiCard')).toHaveCount(6);
  await page.route(/\/api\/news-usage\/overview(?:\?.*)?$/, (route) => route.fulfill({ json: { ...newsPayload(), state: { availability: "not_enabled" }, totals: null } }));
  await page.locator('#mainPeriod [data-range-refresh]').click();
  await expect(page.locator('[data-module="newsTrend"]')).toContainText("利用データはまだありません。");
  await expect(page.locator('.newsUsageDashboard canvas')).toHaveCount(0);
});

test("news requests follow the overview area selection", async ({ page }) => {
  const newsRequests = [];
  await setup(page);
  await page.route(/\/api\/news-usage\/overview(?:\?.*)?$/, (route) => {
    newsRequests.push(new URL(route.request().url()));
    return route.fulfill({ json: newsPayload() });
  });
  await page.goto("/dashboard");
  await expect(page.locator('[data-module="newsTrend"] canvas')).toHaveCount(1);
  await page.locator('#regionRanking [data-area]').first().click();
  await expect.poll(() => newsRequests.at(-1)?.searchParams.get("area_key")).toBeTruthy();
  await expect(page.locator('[data-module="newsTrend"] canvas')).toHaveCount(1);
});
