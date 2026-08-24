const { test, expect } = require("@playwright/test");
const { installApiMocks } = require("./fixtures");

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
  await expect(page.locator('[data-group="overviewModules"]').first()).toContainText("集計停止");
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
  await expect(page.locator("#productNote")).toContainText(
    "正式な製品名を確認できなかった質問 2件",
  );
});

test("an unknown producer category is rejected instead of relabeled", async ({ page }) => {
  await installApiMocks(page, {
    overviewOverride: {
      questionCategories: [{ key: "legacy_unknown", count: 1, rate: 1 }],
    },
  });
  await page.goto("/dashboard");
  await expect(page.locator('[data-group="overviewModules"]').first()).toContainText(
    "未対応の質問タイプ",
  );
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
});

test("a missing user activity contract is rejected instead of shown as dormant", async ({ page }) => {
  await installApiMocks(page, {
    usersOverride: {
      users: [{
        rosterId: "roster_bad",
        name: "契約欠落",
        email: "missing@example.com",
        area: "関西",
        areaKey: "関西",
        labels: [],
        lastActiveAt: "",
        activeDays7: 0,
        questionCount7: 0,
        completeDeliveryRate: null,
        activityLabel: "休眠ユーザー",
      }],
    },
  });
  await page.goto("/dashboard");
  await expect(page.locator('[data-group="userModules"]')).toContainText(
    "activityが不正",
  );
  await expect(page.locator("#regionRanking")).toContainText("関西");
});
