const { test, expect } = require("@playwright/test");
const { installApiMocks } = require("./fixtures");

// Expected geography from DMエリア追加. Values are ordered by prefecture code
// 01–46; Okinawa is intentionally absent from the SVG, not from area totals.
const expectedAreas = [
  "北海道東北", "北海道東北", "北海道東北", "北海道東北", "北海道東北", "北海道東北", "北海道東北",
  "関東B", "関東A", "関東A", "関東A", "関東B", "首都圏A", "首都圏B", "関東A",
  "東海北陸", "東海北陸", "東海北陸", "首都圏A", "首都圏A", "東海北陸", "東海北陸", "東海北陸", "東海北陸",
  "関西", "関西", "関西", "関西", "関西", "関西",
  "中四国", "中四国", "中四国", "中四国", "中四国", "中四国", "中四国", "中四国", "中四国",
  "九州", "九州", "九州", "九州", "九州", "九州", "九州",
];
const newlyMappedCodes = [10, 19, 16, 25, 31, 15, 18, 29, 32, 21, 30, 35, 24];
const filteredSummaryPaths = [
  "/api/analytics/overview", "/api/analytics/overview/users",
  "/api/analytics/environment", "/api/analytics/trend", "/api/news-usage/overview",
];

async function clickPrefecture(page, prefecture) {
  await prefecture.scrollIntoViewIfNeeded();
  // The bounding-box center can land in a lake or outside a concave coastline.
  // Find an actual painted point and send a real pointer click through it.
  const point = await prefecture.evaluate((shape) => {
    const bounds = shape.getBoundingClientRect();
    for (let row = 1; row < 10; row += 1) {
      for (let column = 1; column < 10; column += 1) {
        const x = bounds.left + bounds.width * column / 10;
        const y = bounds.top + bounds.height * row / 10;
        if (shape.contains(document.elementFromPoint(x, y))) return { x, y };
      }
    }
    return null;
  });
  expect(point).not.toBeNull();
  await page.mouse.click(point.x, point.y);
}

for (const cohort of ["all", "dm", "hcs"]) {
  test(`${cohort} map assigns all 46 prefectures and newly covered prefectures filter area totals`, async ({ page }) => {
    const requests = [];
    const regions = [...new Set(expectedAreas)].map((areaKey) => ({
      areaKey, area: areaKey, rosterUsers: 10, activeUsers: 6, questions: 42, adoptionRate: .6, returnRate: .5,
    }));
    await installApiMocks(page, { requests, regionsOverride: { regions } });
    await page.goto(`/dashboard?cohort=${cohort}`);
    await expect(page.locator(`#japanMap .mapRegion[data-code]`)).toHaveCount(46);
    await expect(page.locator("#japanMap .mapNeutral")).toHaveCount(0);
    await expect(page.locator('#japanMap [data-code="47"]')).toHaveCount(0);
    const assignments = await page.locator("#japanMap .prefecture[data-code]").evaluateAll((shapes) =>
      shapes.map((shape) => [Number(shape.dataset.code), shape.dataset.areaKey]).sort((a, b) => a[0] - b[0]));
    expect(assignments).toEqual(expectedAreas.map((area, index) => [index + 1, area]));

    for (const code of newlyMappedCodes) {
      const area = expectedAreas[code - 1];
      const prefecture = page.locator(`#japanMap [data-code="${code}"]`);
      await clickPrefecture(page, prefecture);
      await expect(page.locator("#areaChip")).toContainText(area);
      await expect(prefecture).toHaveAttribute("aria-pressed", "true");
      await expect(page.locator(`[data-cohort="${cohort}"]`)).toHaveAttribute("aria-selected", "true");
      expect(new URL(page.url()).searchParams.get("area")).toBe(area);
      for (const path of filteredSummaryPaths) {
        await expect.poll(() => {
          const query = new URLSearchParams(requests.filter((request) => request.path === path).at(-1)?.search || "");
          return { area: query.get("area_key"), cohort: query.get("cohort") };
        }).toEqual({ area, cohort });
      }
      // All prefectures in the selected area represent the same roster metric.
      await expect(page.locator(`#japanMap .mapRegion[data-area-key="${area}"][aria-pressed="true"]`))
        .toHaveCount(expectedAreas.filter((expected) => expected === area).length);
    }
  });
}

test("map removes the remote-island inset and gives the mainland map visual priority", async ({ page }) => {
  await installApiMocks(page);
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.goto("/dashboard");

  await expect(page.locator("#japanMap .okinawa")).toHaveCount(0);
  await expect(page.locator("#japanMap .boundary-line")).toHaveCount(0);

  const mapPanel = await page.locator('[data-module="map"]').boundingBox();
  const rankingPanel = await page.locator('[data-module="ranking"]').boundingBox();
  const mapSvg = await page.locator("#japanMap svg").boundingBox();
  expect(mapPanel.width).toBeGreaterThan(rankingPanel.width * 1.25);
  expect(mapSvg.height).toBeGreaterThanOrEqual(570);
});

test("map zoom starts at 100 percent, supports local pan and returns exactly to the full view", async ({ page }) => {
  await installApiMocks(page);
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.goto("/dashboard");

  const svg = page.locator("#japanMap svg");
  const zoomOut = page.locator("[data-map-zoom-out]");
  const zoomLevel = page.locator("[data-map-zoom-level]");
  const zoomIn = page.locator("[data-map-zoom-in]");
  const zoomReset = page.locator("[data-map-zoom-reset]");
  const fullViewBox = await svg.getAttribute("viewBox");

  await expect(zoomLevel).toHaveText("100%");
  await expect(zoomOut).toBeDisabled();
  await expect(zoomReset).toBeDisabled();

  for (const expected of ["125%", "150%", "175%", "200%"]) {
    await zoomIn.click();
    await expect(zoomLevel).toHaveText(expected);
  }
  await expect(zoomIn).toBeDisabled();
  await expect(page.locator(".mapTooltip")).toBeHidden();
  expect(await svg.getAttribute("viewBox")).not.toBe(fullViewBox);
  await page.locator(".mapGrid").scrollIntoViewIfNeeded();
  const headingRemainsTopmost = await page.getByRole("heading", { name: "日本利用マップ" }).evaluate((heading) => {
    const bounds = heading.getBoundingClientRect();
    const topmost = document.elementFromPoint(bounds.left + bounds.width / 2, bounds.top + bounds.height / 2);
    return topmost === heading || heading.contains(topmost);
  });
  const legendRemainsTopmost = await page.locator(".mapLegend").evaluate((legend) => {
    const bounds = legend.getBoundingClientRect();
    const topmost = document.elementFromPoint(bounds.left + bounds.width / 2, bounds.top + bounds.height / 2);
    return topmost === legend || legend.contains(topmost);
  });
  expect(headingRemainsTopmost).toBeTruthy();
  expect(legendRemainsTopmost).toBeTruthy();

  const beforeDrag = await svg.getAttribute("viewBox");
  const box = await svg.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 70, box.y + box.height / 2 + 35);
  await page.mouse.up();
  expect(await svg.getAttribute("viewBox")).not.toBe(beforeDrag);
  await expect(page).not.toHaveURL(/area=/);

  await zoomOut.click();
  await expect(zoomLevel).toHaveText("175%");
  await zoomReset.click();
  await expect(zoomLevel).toHaveText("100%");
  await expect(svg).toHaveAttribute("viewBox", fullViewBox);
  await expect(zoomOut).toBeDisabled();
  await expect(zoomReset).toBeDisabled();

  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  for (let index = 0; index < 4; index += 1) await page.locator("[data-map-zoom-in]").click();
  await expect(page.locator("[data-map-zoom-level]")).toHaveText("200%");
  await expect(page.locator(".mapTooltip")).toBeHidden();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
});

test("map hover shows business metrics and click filters the whole summary with one area key", async ({ page }) => {
  const requests = []; await installApiMocks(page, { requests }); await page.goto("/dashboard");
  const kansai = page.locator('[data-area-key="関西"]').first();
  await kansai.hover(); await expect(page.locator(".mapTooltip")).toContainText("利用率");
  await kansai.click(); await expect(page.locator("#areaChip")).toContainText("関西");
  await expect.poll(() => new URLSearchParams(requests.filter((row) => row.path === "/api/analytics/overview").at(-1)?.search || "").get("area_key")).toBe("関西");
  await expect.poll(() => new URLSearchParams(requests.filter((row) => row.path === "/api/analytics/overview/users").at(-1)?.search || "").get("area_key")).toBe("関西");
});

test("map selection works by keyboard and survives reload while browser back restores the full view", async ({ page }) => {
  await installApiMocks(page);
  await page.goto("/dashboard");
  const kansai = page.locator('[data-area-key="関西"]').first();
  await kansai.focus();
  await expect(kansai).toHaveAttribute("role", "button");
  await expect(kansai).toHaveAttribute("aria-label", /関西.*利用率.*再訪率/);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/area=%E9%96%A2%E8%A5%BF/);
  await expect(page.locator("#areaChip")).toContainText("関西");

  await page.reload();
  await expect(page.locator("#areaChip")).toContainText("関西");
  await page.goBack();
  await expect(page).not.toHaveURL(/area=/);
  await expect(page.locator("#areaChip")).toBeEmpty();
  await expect(page.locator("#pageRoot")).toBeFocused();
});
