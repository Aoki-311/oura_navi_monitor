const { test, expect } = require("@playwright/test");
const { installApiMocks } = require("./fixtures");

test("map hover shows business metrics and click filters 69 and 80 scopes with one area key", async ({ page }) => {
  const requests = []; await installApiMocks(page, { requests }); await page.goto("/dashboard");
  const kansai = page.locator('[data-area-key="関西"]').first();
  await kansai.hover(); await expect(page.locator(".mapTooltip")).toContainText("利用率");
  await kansai.click(); await expect(page.locator("#areaChip")).toContainText("関西");
  await expect.poll(() => new URLSearchParams(requests.filter((row) => row.path === "/api/analytics/overview").at(-1)?.search || "").get("area_key")).toBe("関西");
  await expect.poll(() => new URLSearchParams(requests.filter((row) => row.path === "/api/analytics/users").at(-1)?.search || "").get("area_key")).toBe("関西");
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
