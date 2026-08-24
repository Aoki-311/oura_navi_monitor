const { test, expect } = require("@playwright/test");
const { installApiMocks } = require("./fixtures");

test("overview and conversation journey stay usable on PC, iPad and mobile widths", async ({ page }) => {
  await installApiMocks(page);
  for (const viewport of [
    { width: 1440, height: 960 },
    { width: 1024, height: 1366 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport);
    await page.goto("/dashboard");
    await expect(page.getByRole("heading", { name: "全体サマリー" })).toBeVisible();
    await expect(page.locator("#kpis .kpiCard")).toHaveCount(6);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
    ).toBeTruthy();
  }

  await page.goto("/dashboard?page=user&roster=roster_1");
  const journey = page.locator(".conversationJourney");
  await expect(journey.locator(".conversationList")).toBeVisible();
  await journey.locator(".conversationItem").first().click();
  await expect(journey.locator(".messageList")).toContainText("製品の仕様を教えてください");
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBeTruthy();
});
