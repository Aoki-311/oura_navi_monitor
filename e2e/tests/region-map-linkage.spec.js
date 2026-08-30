const { test, expect } = require("@playwright/test");
const { installApiMocks } = require("./fixtures");

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
