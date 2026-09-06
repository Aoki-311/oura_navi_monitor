const { test, expect } = require("@playwright/test");
const { detail, installApiMocks, makeAnalyticsUsers, newsUsage } = require("./fixtures");
const { openPeriod, selectDate, draftPeriod } = require("./date-range-helpers");

test("calendar edits stay draft until apply; inclusive JST dates survive reload and navigation", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, { requests });
  await page.goto("/dashboard?page=user&roster=roster_1");
  await expect(page.locator('[data-news-kpi="tabViews"] strong')).toContainText("10");
  const form = page.locator("#mainPeriod");
  const priorLabel = await form.locator("[data-applied-range]").textContent();
  const count = requests.filter((r) => r.path.startsWith("/api/analytics/")).length;
  await draftPeriod(form, "2026-08-21", "2026-08-23");
  await expect(form.locator("[data-applied-range]")).toHaveText(priorLabel);
  expect(requests.filter((r) => r.path.startsWith("/api/analytics/")).length).toBe(count);
  await form.getByRole("button", { name: "反映", exact: true }).click();
  await expect(page).toHaveURL(/start=2026-08-21&end=2026-08-23/);
  await expect(page.locator("#mainPeriod [data-applied-range]")).toContainText("2026/08/21 — 2026/08/23");
  const request = requests.filter((r) => r.path === "/api/analytics/users/roster_1").at(-1);
  const params = new URLSearchParams(request.search);
  expect(params.get("start")).toBe("2026-08-21T00:00:00+09:00");
  expect(params.get("end")).toBe("2026-08-24T00:00:00+09:00");
  expect(params.has("preset")).toBe(false);
  await page.reload();
  await expect(page.locator("#mainPeriod [name=start]")).toHaveValue("2026-08-21");
  await expect(page.locator('[data-news-kpi="newsContentClicks"] strong')).toContainText("9");
  await page.getByRole("button", { name: "ユーザー管理", exact: true }).click();
  await expect(page.locator("#mainDateRange")).toBeHidden();
  await page.getByRole("button", { name: "ユーザー分析", exact: true }).click();
  await expect(page.locator("#mainPeriod [name=end]")).toHaveValue("2026-08-23");
  await expect(page.locator(".mainNav [data-page=newsusage]")).toHaveCount(0);
});

test("refresh uses the applied range while a different draft is present", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, { requests });
  await page.goto("/dashboard?page=user&roster=roster_1&preset=custom&start=2026-08-20&end=2026-08-23");
  await expect(page.locator('[data-news-kpi="tabViews"]')).toBeVisible();
  await selectDate(page.locator("#mainPeriod"), "2026-08-01");
  await page.locator("#mainPeriod [data-range-refresh]").click();
  await expect(page.locator("#mainPeriod [name=start]")).toHaveValue("2026-08-20");
  await expect(page.locator("#pageRoot")).toHaveAttribute("aria-busy", "false");
  const request = requests.filter((r) => r.path === "/api/analytics/users/roster_1").at(-1);
  expect(new URLSearchParams(request.search).get("start")).toBe("2026-08-20T00:00:00+09:00");
});

test("an incomplete calendar range cannot replace applied metrics and an earlier second date is ordered", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, { requests });
  await page.goto("/dashboard?page=user&roster=roster_1&preset=custom&start=2026-08-20&end=2026-08-23");
  await expect(page.locator('[data-news-kpi="tabViews"]')).toBeVisible();
  const count = requests.filter((r) => r.path === "/api/analytics/users/roster_1").length;
  const form = page.locator("#mainPeriod");
  await selectDate(form, "2026-08-24");
  await expect(form.locator("[data-range-apply]")).toBeDisabled();
  expect(requests.filter((r) => r.path === "/api/analytics/users/roster_1").length).toBe(count);
  await expect(form.locator("[data-applied-range]")).toContainText("2026/08/20 — 2026/08/23");
  await selectDate(form, "2026-08-21");
  await expect(form.locator('[name="start"]')).toHaveValue("2026-08-21");
  await expect(form.locator('[name="end"]')).toHaveValue("2026-08-24");
  await form.locator("[data-range-apply]").click();
  await expect(page).toHaveURL(/start=2026-08-21&end=2026-08-24/);
});

test("personal News cards expose visits, domestic-overseas and society breakdowns without losing Chat", async ({ page }) => {
  await installApiMocks(page);
  await page.goto("/dashboard?page=user&roster=roster_1");
  const visits = page.locator('[data-news-kpi="tabViews"]');
  await expect(visits.locator("strong")).toContainText("10");
  await visits.hover();
  await expect(visits.locator('[role="tooltip"]')).toContainText("ニュース6回");
  await expect(visits.locator('[role="tooltip"]')).toContainText("学会情報4回");
  const news = page.locator('[data-news-kpi="newsContentClicks"]');
  await news.focus();
  await expect(news.locator('[role="tooltip"]')).toContainText("国内6回");
  await expect(news.locator('[role="tooltip"]')).toContainText("海外3回");
  const society = page.locator('[data-news-kpi="societyContentClicks"]');
  await society.focus();
  await expect(society.locator('[role="tooltip"]')).toContainText("糖尿病関連5回");
  await expect(page.locator('[data-module="summary"]')).toContainText("期間内質問数");
  await expect(page.locator('[data-module="summary"] [data-module="news"] .newsKpiCard')).toHaveCount(3);
  await expect(page.locator('[data-module="summary"]')).not.toContainText("18/20件");
  await expect(page.locator("main")).not.toContainText(/\d+\s*\/\s*\d+.*計測|部分記録|一部計測|途中集計/);
  await expect(page.locator("main .measurementNote")).toHaveCount(0);
  await expect(page.locator(".messageList")).toContainText("製品の仕様を教えてください");
});

for (const state of ["not_enabled", "before_measurement", "unavailable"]) {
  test(`${state} News data stays distinct from zero while personal Chat remains usable`, async ({ page }) => {
    await installApiMocks(page, { newsUsageOverride: { state: { availability: state }, totals: null } });
    await page.goto("/dashboard?page=user&roster=roster_1");
    const panel = page.locator('[data-module="news"]');
    await expect(panel).toContainText(state === "not_enabled" ? "計測はまだ始まっていません" : state === "before_measurement" ? "計測開始前" : "取得できません");
    await expect(panel.locator(".newsKpiCard")).toHaveCount(0);
    await expect(page.locator('[data-module="summary"]')).toContainText("期間内質問数");
  });
}

test("selecting a user gives immediate feedback and back restores chooser search and page", async ({ page }) => {
  await installApiMocks(page, { usersOverride: { users: makeAnalyticsUsers(80) } });
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  await page.route(/\/api\/analytics\/users\/roster_16(?:\?.*)?$/, async (route) => {
    await gate;
    await route.fulfill({ json: { ...detail, profile: { ...detail.profile, rosterId: "roster_16", name: "利用者 16" } } });
  });
  await page.goto("/dashboard?page=user&user_q=利用者&user_page=2");
  const chosen = page.locator('.userChoice[data-roster="roster_16"]');
  await expect(chosen).toBeVisible();
  await chosen.click();
  await expect(chosen).toHaveAttribute("aria-busy", "true");
  await expect(page.locator("[data-pending-user] .loadingSpinner")).toBeVisible();
  release();
  await expect(page).toHaveURL(/roster=roster_16/);
  await page.getByRole("button", { name: "ユーザー一覧に戻る" }).click();
  await expect(page.locator("#userSearch")).toHaveValue("利用者");
  await expect(page.locator("#userChooserPagination")).toContainText("16–30 / 80名");
  await expect(page.locator("[data-pending-user]")).toHaveCount(0);
});

test("personal dashboard and date calendar fit mobile without horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installApiMocks(page);
  await page.goto("/dashboard?page=user&roster=roster_1");
  await expect(page.locator('[data-news-kpi="societyContentClicks"]')).toBeVisible();
  await openPeriod(page.locator("#mainPeriod"));
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
});


test("a pending personal News read completes when a Chat refresh fails", async ({ page }) => {
  await installApiMocks(page);
  let releaseNews;
  const gate = new Promise((resolve) => { releaseNews = resolve; });
  await page.route(/\/api\/news-usage\/users\/roster_1(?:\?.*)?$/, async (route) => {
    await gate;
    return route.fulfill({ json: { ...newsUsage, scope: "user_map", rosterId: "roster_1" } });
  });
  await page.goto("/dashboard?page=user&roster=roster_1");
  await expect(page.locator('[data-module="summary"]')).toContainText("期間内質問数");
  await expect(page.locator('[data-module="news"]')).toHaveAttribute("aria-busy", "true");
  await page.route(/\/api\/analytics\/users\/roster_1(?:\?.*)?$/, (route) => route.fulfill({ status: 503, json: { detail: "unavailable" } }));
  await page.locator("#mainPeriod [data-range-refresh]").click();
  await expect(page.locator("[data-user-anchor-error]")).toBeVisible();
  releaseNews();
  await expect(page.locator('[data-news-kpi="tabViews"] strong')).toContainText("10");
  await expect(page.locator('[data-module="news"]')).toHaveAttribute("aria-busy", "false");
});


for (const width of [1440, 390]) {
  test(`personal News hover is fully visible with five society rows at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 960 });
    const categories = ["糖尿病関連", "循環器関連", "がん・腫瘍関連", "輸液・栄養・代謝関連", "その他"];
    await installApiMocks(page, { newsUsageOverride: { societyCategories: categories.map((label) => ({ key: label, label, clicks: 1, sources: [{ key: label, label, clicks: 1 }] })) } });
    await page.goto("/dashboard?page=user&roster=roster_1");
    await expect(page.locator('[data-news-kpi="societyContentClicks"]')).toBeVisible();
    for (const key of ["newsContentClicks", "societyContentClicks"]) {
      const card = page.locator(`[data-news-kpi="${key}"]`);
      await card.evaluate((node) => node.scrollIntoView({ block: "center" }));
      await card.focus();
      const popup = card.locator('[role="tooltip"]');
      await expect(popup).toBeVisible();
      const hit = await popup.evaluate((node) => {
        const box = node.getBoundingClientRect();
        return { withinViewport: box.top >= 0 && box.bottom <= innerHeight && box.left >= 0 && box.right <= innerWidth, rowsVisible: [...node.children].every((row) => { const r = row.getBoundingClientRect(); return r.top >= box.top && r.bottom <= box.bottom && row.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)); }) };
      });
      expect(hit).toEqual({ withinViewport: true, rowsVisible: true });
      await expect(popup.locator(":scope > span")).toHaveCount(key === "newsContentClicks" ? 2 : 5);
    }
  });
}
