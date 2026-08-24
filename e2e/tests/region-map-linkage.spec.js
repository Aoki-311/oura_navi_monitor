const { test, expect } = require("@playwright/test");
const { installApiMocks } = require("./fixtures");

test("map hover shows business metrics and click filters 69 and 80 scopes with one area key", async ({ page }) => {
  const requests = []; await installApiMocks(page, { requests }); await page.goto("/dashboard");
  const kansai = page.locator('[data-area-key="関西"]');
  await kansai.hover(); await expect(page.locator(".mapTooltip")).toContainText("利用率");
  await kansai.click(); await expect(page.locator("#areaChip")).toContainText("関西");
  await expect.poll(() => new URLSearchParams(requests.filter((row) => row.path === "/api/analytics/overview").at(-1)?.search || "").get("area_key")).toBe("関西");
  await expect.poll(() => new URLSearchParams(requests.filter((row) => row.path === "/api/analytics/users").at(-1)?.search || "").get("area_key")).toBe("関西");
});
