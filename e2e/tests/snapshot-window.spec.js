const { test, expect } = require("@playwright/test");
const {
  installApiMocks, overviewUsers, regions,
} = require("./fixtures");

const otherWindow = {
  windowStart: "2026-08-09T15:00:00Z",
  windowEnd: "2026-08-16T01:00:00Z",
  windowTimezone: "Asia/Tokyo",
};

test("an initial cross-module window mismatch isolates the mismatched module and disables CSV", async ({ page }) => {
  await installApiMocks(page, {
    regionsOverride: {
      ...otherWindow,
      regions: [{
        areaKey: "異期間", area: "異なる期間の地域", rosterUsers: 1,
        activeUsers: 1, questions: 999, adoptionRate: 1, returnRate: 1,
      }],
    },
  });

  await page.goto("/dashboard");

  await expect(page.locator('[data-module="kpis"] .kpiCard').first()).toContainText("24");
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.locator('[data-module="ranking"]')).toContainText("同じ公開データ版を取得できませんでした");
  await expect(page.locator('[data-module="ranking"]')).not.toContainText("異なる期間の地域");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

test("a later user-window mismatch preserves the committed Summary body and disables CSV", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, {
    requests,
    overviewUsersByQuery: {
      "window-mismatch": {
        ...otherWindow,
        users: [{ ...overviewUsers.users[0], name: "異なる期間のユーザー" }],
      },
    },
  });

  await page.goto("/dashboard");
  await expect(page.locator('[data-module="kpis"] .kpiCard').first()).toContainText("24");
  await expect(page.locator("#regionRanking")).toContainText(regions.regions[0].area);
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.getByRole("button", { name: "CSV" })).toBeEnabled();

  await page.locator("#overviewUserSearch").fill("window-mismatch");

  await expect(page.locator("[data-freshness-banner]")).toContainText("同じ公開データ版を取得できませんでした");
  await expect(page.locator('[data-module="kpis"] .kpiCard').first()).toContainText("24");
  await expect(page.locator("#regionRanking")).toContainText(regions.regions[0].area);
  await expect(page.locator("#overviewUsers")).toContainText("山田 太郎");
  await expect(page.locator("#overviewUsers")).not.toContainText("異なる期間のユーザー");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
  expect(requests.filter((row) => row.path === "/api/analytics/overview")).toHaveLength(4);
  expect(requests.filter((row) => row.path === "/api/analytics/regions")).toHaveLength(4);
  expect(requests.filter((row) => row.path === "/api/analytics/overview/users")).toHaveLength(5);
});
