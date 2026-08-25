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

test("conversation failure stays inside the journey while personal analytics remains usable", async ({ page }) => {
  await installApiMocks(page, { failConversations: true });
  await page.goto("/dashboard?page=user&roster=roster_1");
  await expect(page.locator('[data-module="profile"]')).toContainText("山田 太郎");
  await expect(page.locator('[data-module="summary"]')).toContainText("回答成功率");
  await expect(page.locator('[data-module="conversations"]')).toContainText("データを読み込めませんでした");
});

test("personal analytics failure does not remove an available conversation journey", async ({ page }) => {
  await installApiMocks(page, { failDetail: true });
  await page.goto("/dashboard?page=user&roster=roster_1");
  await expect(page.locator('[data-module="profile"]')).toContainText("データを読み込めませんでした");
  await expect(page.locator("#conversationList")).toContainText("製品情報の確認");
});
