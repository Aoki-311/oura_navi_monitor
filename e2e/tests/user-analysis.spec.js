const { test, expect } = require("@playwright/test");
const { installApiMocks } = require("./fixtures");

test("user detail keeps profile, demand portrait and conversation-message double pane", async ({ page }) => {
  await installApiMocks(page); await page.goto("/dashboard?page=user&roster=roster_1");
  await expect(page.locator("main")).toContainText("個人利用サマリー");
  await expect(page.locator("main")).toContainText("同じ地域平均");
  await expect(page.locator("main")).toContainText("ユーザーニーズ傾向");
  await expect(page.locator("main")).toContainText("情報確認");
  await page.locator(".conversationItem").click();
  await expect(page.locator(".messageList")).toContainText("製品の仕様を教えてください");
  await expect(page.locator(".conversationJourney .conversationList")).toBeVisible();
  await expect(page.locator(".conversationJourney .messageList")).toBeVisible();
});
