const { test, expect } = require("@playwright/test");
const { installApiMocks, overview } = require("./fixtures");
const { openPeriod, showMonth, selectDate, draftPeriod, applyPreset } = require("./date-range-helpers");

const initialQuery = "preset=custom&start=2026-08-20&end=2026-08-23";
const personalPath = `/dashboard?page=user&roster=roster_1&${initialQuery}`;
const detailRequests = (requests) => requests.filter((row) => row.path === "/api/analytics/users/roster_1");

test.beforeEach(async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-09-06T03:00:00Z"));
});

for (const dismiss of ["cancel", "outside", "escape"]) {
  test(`${dismiss} closes the period popup and discards an unapplied range`, async ({ page }) => {
    const requests = [];
    await installApiMocks(page, { requests });
    await page.goto(personalPath);
    await expect(page.locator('[data-news-kpi="tabViews"]')).toBeVisible();
    const form = page.locator("#mainPeriod");
    await expect(form.locator("[data-range-popup]")).toBeHidden();
    await expect(form.locator("[data-range-trigger]")).toHaveAttribute("aria-expanded", "false");
    const before = detailRequests(requests).length;
    await draftPeriod(form, "2026-08-03", "2026-08-07");
    await expect(form.locator("[data-range-trigger]")).toHaveAttribute("aria-expanded", "true");
    await expect(form.locator("[data-applied-range]")).toHaveText("2026/08/20 — 2026/08/23");
    if (dismiss === "cancel") await form.locator("[data-range-cancel]").click();
    else if (dismiss === "outside") await page.locator(".appHeader").click({ position: { x: 10, y: 10 } });
    else await page.keyboard.press("Escape");
    await expect(form.locator("[data-range-popup]")).toBeHidden();
    await expect(form.locator("[data-range-trigger]")).toHaveAttribute("aria-expanded", "false");
    expect(detailRequests(requests)).toHaveLength(before);
    await expect(page).toHaveURL(/start=2026-08-20&end=2026-08-23/);
    await openPeriod(form);
    await expect(form.locator('[name="start"]')).toHaveValue("2026-08-20");
    await expect(form.locator('[name="end"]')).toHaveValue("2026-08-23");
  });
}

test("the calendar highlights a range across months and applies inclusive JST bounds once", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, { requests });
  await page.goto(personalPath);
  await expect(page.locator('[data-news-kpi="tabViews"]')).toBeVisible();
  const form = page.locator("#mainPeriod");
  const before = detailRequests(requests).length;
  await selectDate(form, "2026-08-10");
  await form.locator('[data-date="2026-08-12"]').hover();
  await expect(form.locator(".rangePreview [data-date]")).toHaveCount(3);
  expect(await form.locator(".rangePreview [data-date]").evaluateAll((nodes) => nodes.map((node) => node.dataset.date))).toEqual(["2026-08-10", "2026-08-11", "2026-08-12"]);
  await form.locator("[data-range-clear]").click();
  await selectDate(form, "2026-08-30");
  await expect(form.locator("[data-range-apply]")).toBeDisabled();
  await selectDate(form, "2026-09-03");
  await expect(form.locator("[data-calendar-month]")).toHaveText("2026年9月");
  for (const date of ["2026-09-01", "2026-09-02", "2026-09-03"]) {
    await expect(form.locator(`[data-date="${date}"]`)).toHaveAttribute("aria-pressed", "true");
  }
  await expect(form.locator('[data-date="2026-09-04"]')).toHaveAttribute("aria-pressed", "false");
  await showMonth(form, "2026-08-30");
  await expect(form.locator('[data-date="2026-08-30"]')).toHaveAttribute("aria-pressed", "true");
  await expect(form.locator('[data-date="2026-08-31"]')).toHaveAttribute("aria-pressed", "true");
  await expect(form.locator('[data-date="2026-08-29"]')).toHaveAttribute("aria-pressed", "false");
  expect(detailRequests(requests)).toHaveLength(before);
  await form.locator("[data-range-apply]").click();
  await expect(form.locator("[data-range-popup]")).toBeHidden();
  await expect(page).toHaveURL(/start=2026-08-30&end=2026-09-03/);
  await expect.poll(() => detailRequests(requests).length).toBe(before + 1);
  const bounds = new URLSearchParams(detailRequests(requests).at(-1).search);
  expect(bounds.get("start")).toBe("2026-08-30T00:00:00+09:00");
  expect(bounds.get("end")).toBe("2026-09-04T00:00:00+09:00");
  await expect(form.locator("[data-applied-range]")).toHaveText("2026/08/30 — 2026/09/03");
});

test("keyboard navigation chooses range dates and future dates cannot be selected", async ({ page }) => {
  await installApiMocks(page);
  await page.goto(personalPath);
  await expect(page.locator('[data-news-kpi="tabViews"]')).toBeVisible();
  const form = page.locator("#mainPeriod");
  await selectDate(form, "2026-08-21", { keyboard: true });
  await form.locator('[data-date="2026-08-21"]').press("ArrowRight");
  await expect(form.locator('[data-date="2026-08-22"]')).toBeFocused();
  await page.keyboard.press("Space");
  await expect(form.locator('[name="start"]')).toHaveValue("2026-08-21");
  await expect(form.locator('[name="end"]')).toHaveValue("2026-08-22");
  await form.locator("[data-range-apply]").click();
  await expect(page).toHaveURL(/start=2026-08-21&end=2026-08-22/);
  await showMonth(form, "2026-09-06");
  await expect(form.locator('[data-date="2026-09-06"]')).toBeEnabled();
  await expect(form.locator('[data-date="2026-09-07"]')).toBeDisabled();
  await expect(form.locator("[data-calendar-next]")).toBeDisabled();
});

test("clear resets only the draft and preset selections remain explicit before applying", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, { requests });
  await page.goto(personalPath);
  await expect(page.locator('[data-news-kpi="tabViews"]')).toBeVisible();
  const form = page.locator("#mainPeriod");
  await openPeriod(form);
  const before = detailRequests(requests).length;
  await form.locator("[data-range-clear]").click();
  await expect(form.locator('[name="start"]')).toHaveValue("");
  await expect(form.locator('[name="end"]')).toHaveValue("");
  await expect(form.locator("[data-range-apply]")).toBeDisabled();
  await expect(form.locator("[data-applied-range]")).toHaveText("2026/08/20 — 2026/08/23");
  for (const [preset, start] of [["today", "2026-09-06"], ["last_7d", "2026-08-31"], ["last_14d", "2026-08-24"], ["last_30d", "2026-08-08"]]) {
    await form.locator(`[data-range-preset="${preset}"]`).click();
    await expect(form.locator('[name="start"]')).toHaveValue(start);
    await expect(form.locator('[name="end"]')).toHaveValue("2026-09-06");
    await expect(form.locator(`[data-range-preset="${preset}"]`)).toHaveAttribute("aria-pressed", "true");
    expect(detailRequests(requests)).toHaveLength(before);
  }
  await form.locator("[data-range-apply]").click();
  await expect(page).toHaveURL(/preset=last_30d/);
  await expect.poll(() => detailRequests(requests).length).toBe(before + 1);
});

test("the shared popover applies module dates independently and main changes preserve them", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, { requests });
  await page.goto(`/dashboard?${initialQuery}`);
  await expect(page.locator("#deviceChart")).toBeVisible();
  await expect(page.locator("#usageChart")).toHaveCount(1);
  const before = requests.length;
  const environment = page.locator("#environment-period");
  await draftPeriod(environment, "2026-08-05", "2026-08-11");
  await environment.locator("[data-range-apply]").click();
  await expect(page).toHaveURL(/env_start=2026-08-05/);
  await expect.poll(() => requests.slice(before).map((row) => row.path)).toEqual(["/api/analytics/environment"]);
  const second = requests.length;
  await applyPreset(page.locator("#usage-period"), "last_14d");
  await expect(page).toHaveURL(/trend_preset=last_14d/);
  await expect.poll(() => requests.slice(second).map((row) => row.path)).toEqual(["/api/analytics/trend"]);
  const deviceCanvas = await page.locator("#deviceChart").elementHandle();
  const usageCanvas = await page.locator("#usageChart").elementHandle();
  const beforeMain = requests.length;
  await applyPreset(page.locator("#mainPeriod"), "today");
  await expect(page).toHaveURL(/preset=today/);
  await expect(page.locator("#pageRoot")).toHaveAttribute("aria-busy", "false");
  expect(requests.slice(beforeMain).filter((row) => ["/api/analytics/environment", "/api/analytics/trend"].includes(row.path))).toHaveLength(0);
  await expect(page.locator("#environment-period [data-applied-range]")).toHaveText("2026/08/05 — 2026/08/11");
  await expect(page.locator("#usage-period [data-applied-range]")).toHaveText("2026/08/24 — 2026/09/06");
  expect(await deviceCanvas.evaluate((node) => node.isConnected && document.querySelector("#deviceChart") === node)).toBe(true);
  expect(await usageCanvas.evaluate((node) => node.isConnected && document.querySelector("#usageChart") === node)).toBe(true);
  const beforeRefresh = requests.length;
  await page.locator("#mainPeriod [data-range-refresh]").click();
  await expect.poll(() => requests.slice(beforeRefresh).filter((row) => row.path === "/api/analytics/overview").length).toBe(1);
  await expect(page.locator("#pageRoot")).toHaveAttribute("aria-busy", "false");
  expect(requests.slice(beforeRefresh).filter((row) => ["/api/analytics/environment", "/api/analytics/trend"].includes(row.path))).toHaveLength(0);
  expect(await deviceCanvas.evaluate((node) => node.isConnected && document.querySelector("#deviceChart") === node)).toBe(true);
  expect(await usageCanvas.evaluate((node) => node.isConnected && document.querySelector("#usageChart") === node)).toBe(true);
  await page.reload();
  await expect(page.locator("#environment-period [data-applied-range]")).toHaveText("2026/08/05 — 2026/08/11");
  await expect(page.locator("#usage-period [data-applied-range]")).toHaveText("2026/08/24 — 2026/09/06");
});

test("a completed environment update preserves the usage popup and its pending draft", async ({ page }) => {
  await installApiMocks(page);
  let release;
  let pending = false;
  const gate = new Promise((resolve) => { release = resolve; });
  await page.route(/\/api\/analytics\/environment(?:\?.*)?$/, async (route) => {
    if (new URL(route.request().url()).searchParams.get("preset") !== "last_14d") return route.fallback();
    pending = true;
    await gate;
    return route.fallback();
  });
  await page.goto(`/dashboard?${initialQuery}`);
  await expect(page.locator("#deviceChart")).toBeVisible();
  await expect(page.locator("#usageChart")).toHaveCount(1);
  await applyPreset(page.locator("#environment-period"), "last_14d");
  await expect.poll(() => pending).toBe(true);
  const usage = page.locator("#usage-period");
  await draftPeriod(usage, "2026-08-10", "2026-08-12");
  const draftPopup = await usage.locator("[data-range-popup]").elementHandle();
  release();
  await expect(page).toHaveURL(/env_preset=last_14d/);
  await expect(page.locator('[data-module="environment"]')).toHaveAttribute("aria-busy", "false");
  expect(await draftPopup.evaluate((node) => node.isConnected && document.querySelector("#usage-period [data-range-popup]") === node)).toBe(true);
  await expect(usage.locator("[data-range-popup]")).toBeVisible();
  await expect(usage.locator('[name="start"]')).toHaveValue("2026-08-10");
  await expect(usage.locator('[name="end"]')).toHaveValue("2026-08-12");
  await usage.locator("[data-range-apply]").click();
  await expect(page).toHaveURL(/trend_start=2026-08-10&trend_end=2026-08-12/);
});

test("a delayed main refresh preserves an open environment popup and its pending draft", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, { requests });
  let release;
  let delayRefresh = false;
  let pending = false;
  const gate = new Promise((resolve) => { release = resolve; });
  await page.route(/\/api\/analytics\/overview(?:\?.*)?$/, async (route) => {
    if (!delayRefresh) return route.fallback();
    pending = true;
    await gate;
    return route.fallback();
  });
  await page.goto(`/dashboard?${initialQuery}`);
  await expect(page.locator("#deviceChart")).toBeVisible();
  await expect(page.locator("#pageRoot")).toHaveAttribute("aria-busy", "false");
  delayRefresh = true;
  await page.locator("#mainPeriod [data-range-refresh]").click();
  await expect.poll(() => pending).toBe(true);
  const environment = page.locator("#environment-period");
  await draftPeriod(environment, "2026-08-01", "2026-08-05");
  const popup = await environment.locator("[data-range-popup]").elementHandle();
  release();
  await expect(page.locator("#pageRoot")).toHaveAttribute("aria-busy", "false");
  expect(await popup.evaluate((node) => node.isConnected && document.querySelector("#environment-period [data-range-popup]") === node)).toBe(true);
  await expect(environment.locator("[data-range-popup]")).toBeVisible();
  await expect(environment.locator("[data-range-trigger]")).toHaveAttribute("aria-expanded", "true");
  await expect(environment.locator('[name="start"]')).toHaveValue("2026-08-01");
  await expect(environment.locator('[name="end"]')).toHaveValue("2026-08-05");
  const beforeApply = requests.length;
  await environment.locator("[data-range-apply]").click();
  await expect(page).toHaveURL(/env_start=2026-08-01&env_end=2026-08-05/);
  const updated = requests.slice(beforeApply).filter((row) => row.path === "/api/analytics/environment");
  expect(updated).toHaveLength(1);
  expect(new URLSearchParams(updated[0].search).get("start")).toBe("2026-08-01T00:00:00+09:00");
  expect(new URLSearchParams(updated[0].search).get("end")).toBe("2026-08-06T00:00:00+09:00");
});

test("an initial main response cannot overwrite a newer independently applied environment result", async ({ page }) => {
  await installApiMocks(page);
  let releaseMain;
  let mainPending = false;
  let initialEnvironmentCompleted = false;
  const gate = new Promise((resolve) => { releaseMain = resolve; });
  await page.route(/\/api\/analytics\/overview(?:\?.*)?$/, async (route) => {
    mainPending = true;
    await gate;
    return route.fallback();
  });
  await page.route(/\/api\/analytics\/environment(?:\?.*)?$/, async (route) => {
    const latest = new URL(route.request().url()).searchParams.get("preset") === "last_14d";
    const raw = JSON.parse(JSON.stringify(overview));
    raw.deviceDistribution = [{ key: "desktop", label: "PC", count: latest ? 140 : 70, rate: 1 }];
    await route.fulfill({ json: raw });
    if (!latest) initialEnvironmentCompleted = true;
  });
  await page.goto(`/dashboard?${initialQuery}`);
  await expect.poll(() => mainPending && initialEnvironmentCompleted).toBe(true);
  await applyPreset(page.locator("#environment-period"), "last_14d");
  await expect(page).toHaveURL(/env_preset=last_14d/);
  const count = () => page.locator("#deviceChart").evaluate((node) => window.Chart.getChart(node).data.datasets[0].data[0]);
  await expect.poll(count).toBe(140);
  releaseMain();
  await expect(page.locator("#pageRoot")).toHaveAttribute("aria-busy", "false");
  await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
  await expect.poll(count).toBe(140);
  await expect(page.locator("#environment-period [data-applied-range]")).toHaveText("2026/08/24 — 2026/09/06");
});

test("a delayed main period apply preserves a newer module date in the URL and after reload", async ({ page }) => {
  await installApiMocks(page);
  let releaseMain;
  let mainPending = false;
  const gate = new Promise((resolve) => { releaseMain = resolve; });
  await page.route(/\/api\/analytics\/overview(?:\?.*)?$/, async (route) => {
    if (new URL(route.request().url()).searchParams.get("preset") !== "today") return route.fallback();
    mainPending = true;
    await gate;
    return route.fallback();
  });
  await page.goto(`/dashboard?${initialQuery}`);
  await expect(page.locator("#deviceChart")).toBeVisible();
  await expect(page.locator("#pageRoot")).toHaveAttribute("aria-busy", "false");
  await applyPreset(page.locator("#mainPeriod"), "today");
  await expect.poll(() => mainPending).toBe(true);
  await applyPreset(page.locator("#environment-period"), "last_14d");
  await expect(page).toHaveURL(/env_preset=last_14d/);
  await expect(page.locator("#environment-period [data-applied-range]")).toHaveText("2026/08/24 — 2026/09/06");
  releaseMain();
  await expect(page.locator("#pageRoot")).toHaveAttribute("aria-busy", "false");
  await expect(page).toHaveURL(/preset=today/);
  await expect(page).toHaveURL(/env_preset=last_14d/);
  await expect(page).toHaveURL(/env_start=2026-08-24&env_end=2026-09-06/);
  await expect(page.locator("#environment-period [data-applied-range]")).toHaveText("2026/08/24 — 2026/09/06");
  await page.reload();
  await expect(page.locator("#deviceChart")).toBeVisible();
  await expect(page.locator("#mainPeriod [data-applied-range]")).toHaveText("2026/09/06");
  await expect(page.locator("#environment-period [data-applied-range]")).toHaveText("2026/08/24 — 2026/09/06");
});

for (const width of [1440, 390]) {
  test(`period popovers remain fully inside the viewport at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 844 });
    await installApiMocks(page);
    await page.goto(`/dashboard?${initialQuery}`);
    await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
    for (const id of ["mainPeriod", "environment-period", "usage-period"]) {
      const form = page.locator(`#${id}`);
      const popup = await openPeriod(form);
      const box = await popup.boundingBox();
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.y).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(width);
      expect(box.y + box.height).toBeLessThanOrEqual(844);
      await expect(form.locator("[data-range-apply]")).toBeVisible();
      await expect(form.locator("[data-range-cancel]")).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
      if (process.env.MONITOR_E2E_CAPTURE_ARTIFACTS === "1") {
        await page.screenshot({ path: testInfo.outputPath(`${id}-${width}.png`) });
      }
      await page.keyboard.press("Escape");
      await expect(popup).toBeHidden();
    }
  });
}
