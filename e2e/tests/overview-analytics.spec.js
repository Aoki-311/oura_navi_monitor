const { test, expect } = require("@playwright/test");
const { installApiMocks, makeAnalyticsUsers, overview } = require("./fixtures");

test("overview renders seven analytics modules and preserves charts after refresh", async ({ page }) => {
  await installApiMocks(page);
  await page.goto("/dashboard");
  for (const title of ["主要KPI", "利用環境・モード", "利用推移", "活性度分布", "ユーザー一覧", "日本利用マップ", "製品ニーズ"]) await expect(page.locator("main")).toContainText(title);
  await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
  await expect(page.locator("#overviewUsers tbody tr")).toHaveCount(2);
  await expect(page.locator("main")).toContainText("製品 × 依頼タスク");
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

test("historical environment gaps are explained without fake unknown charts", async ({ page }) => {
  await installApiMocks(page, {
    overviewOverride: {
      deviceDistribution: [],
      deviceMeasurement: { measuredCount: 0, totalCount: 77, measurementState: "not_measured" },
      modeDistribution: [],
      modeMeasurement: { measuredCount: 0, totalCount: 77, measurementState: "not_measured" },
    },
  });
  await page.goto("/dashboard");
  const environment = page.locator('[data-module="environment"]');
  await expect(environment.getByText("履歴未計測")).toHaveCount(2);
  await expect(environment.locator("#deviceChart")).toHaveCount(0);
  await expect(environment.locator("#modeChart")).toHaveCount(0);
  await expect(page.locator('[data-module="kpis"] .kpiCard')).toHaveCount(6);
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
        completeDelivery: { value: null, measuredCount: 0, totalCount: 0, measurementState: "no_usage" },
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
        completeDelivery: { value: null, measuredCount: 0, totalCount: 0, measurementState: "no_usage" },
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

test("the real 80-person overview is paginated and never rendered as one long table", async ({ page }) => {
  await installApiMocks(page, { usersOverride: { users: makeAnalyticsUsers(80) } });
  await page.goto("/dashboard");
  await expect(page.locator("#overviewUsers tbody tr")).toHaveCount(15);
  await expect(page.locator('[data-module="users"]')).toContainText("1–15 / 80名");
  await page.locator("#overviewSort").selectOption("name_asc");
  await page.getByRole("button", { name: "次のページ" }).click();
  await expect(page.locator("#overviewUsers tbody tr").first()).toContainText("利用者 16");
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
  await expect(card).toContainText("履歴未計測");
  await expect(card).not.toContainText("0.0%");
});

function measurementForTest(value, measuredCount, totalCount, measurementState) {
  return { value, measuredCount, totalCount, measurementState };
}
