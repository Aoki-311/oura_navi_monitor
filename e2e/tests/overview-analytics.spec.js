const { test, expect } = require("@playwright/test");
const { installApiMocks, overview } = require("./fixtures");

test("overview renders seven analytics modules and preserves charts after refresh", async ({ page }) => {
  await installApiMocks(page);
  await page.goto("/dashboard");
  for (const title of ["主要KPI", "利用環境・モード", "利用推移", "活性度分布", "ユーザー一覧", "日本利用マップ", "製品ニーズ"]) await expect(page.locator("main")).toContainText(title);
  await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
  await expect(page.locator("#overviewUsers tbody tr")).toHaveCount(2);
  await expect(page.locator("main")).toContainText("製品情報・仕様");
  expect(await page.locator("canvas").count()).toBeGreaterThanOrEqual(9);
  for (let index = 0; index < 6; index += 1) await page.getByRole("button", { name: "再読込" }).click();
  await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
});

test("one analytics API failure stays local and does not turn missing data into zero", async ({ page }) => {
  await installApiMocks(page, { failOverview: true });
  await page.goto("/dashboard");
  await expect(page.locator('[data-module="kpis"]')).toContainText("データを読み込めませんでした");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.locator("#regionRanking")).toContainText("関西");
});

test("unresolved product candidates are disclosed beside product analytics", async ({ page }) => {
  await installApiMocks(page, {
    overviewOverride: {
      productResolution: {
        candidateCount: 12,
        resolvedCount: 10,
        unresolvedQuestions: 2,
        resolutionRate: 10 / 12,
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
        area: "関西",
        areaKey: "関西",
        labels: [],
        lastActiveAt: "",
        activeDays7: 0,
        userMessageCount7: 0,
        completeDelivery: { value: null, measuredCount: 0, totalCount: 0 },
        activity: "dormant",
        activityLabel: "休眠ユーザー",
      }, {
        rosterId: "roster_bad",
        name: "契約欠落",
        email: "missing@example.com",
        area: "関西",
        areaKey: "関西",
        labels: [],
        lastActiveAt: "",
        activeDays7: 0,
        userMessageCount7: 0,
        completeDelivery: { value: null, measuredCount: 0, totalCount: 0 },
        activityLabel: "休眠ユーザー",
      }],
    },
  });
  await page.goto("/dashboard");
  await expect(page.locator("#overviewUsers")).toContainText("正常ユーザー");
  await expect(page.locator("#overviewUsers")).toContainText("契約欠落");
  await expect(page.locator("#regionRanking")).toContainText("関西");
});

test("stale freshness metadata never hides otherwise available data", async ({ page }) => {
  await installApiMocks(page, {
    overviewOverride: { freshness: { state: "stale", dataThrough: "2026-08-20T00:00:00Z" } },
    usersOverride: { freshness: { state: "stale", dataThrough: "2026-08-20T00:00:00Z" } },
  });
  await page.goto("/dashboard");
  await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.locator("#regionRanking")).toContainText("関西");
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
