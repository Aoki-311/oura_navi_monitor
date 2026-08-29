const { test, expect } = require("@playwright/test");
const { installApiMocks, makeAnalyticsUsers } = require("./fixtures");

test("user detail keeps profile, demand portrait and conversation-message double pane", async ({ page }) => {
  await installApiMocks(page); await page.goto("/dashboard?page=user&roster=roster_1");
  await expect(page.locator("main")).toContainText("個人利用サマリー");
  await expect(page.locator("main")).toContainText("同じ地域 · 関西");
  await expect(page.locator("main")).toContainText("ユーザーニーズ傾向");
  await expect(page.locator("main")).toContainText("情報確認");
  await expect(page.locator(".messageList")).toContainText("製品の仕様を教えてください");
  await expect(page.locator(".conversationJourney .conversationList")).toBeVisible();
  await expect(page.locator(".conversationJourney .messageList")).toBeVisible();
  await expect(page.locator('[data-module="summary"]')).toContainText("8 / 10名を計測");
  await expect(page.locator('[data-module="summary"]')).not.toContainText("8 / 10件を計測");
});

test("historical mode and device gaps are explained without fake unknown charts", async ({ page }) => {
  await installApiMocks(page, {
    detailOverride: {
      modes: [],
      modeMeasurement: { measuredCount: 0, totalCount: 20, measurementState: "not_measured" },
      devices: [],
      deviceMeasurement: { measuredCount: 0, totalCount: 20, measurementState: "not_measured" },
    },
  });
  await page.goto("/dashboard?page=user&roster=roster_1");
  await expect(page.locator("[data-freshness-banner]")).toContainText("3時間ごと");
  await expect(page.locator('[data-module="trend"]')).toContainText("途中集計");
  const needs = page.locator('[data-module="needs"]');
  await expect(needs).toContainText("モード");
  await expect(needs).toContainText("デバイス");
  await expect(needs.getByText("履歴未計測")).toHaveCount(2);
  await expect(needs.locator("#personalModes")).toHaveCount(0);
  await expect(needs.locator("#personalDevices")).toHaveCount(0);
});

test("malformed detail metadata never erases a valid personal analysis transaction", async ({ page }) => {
  await installApiMocks(page, {
    detailOverride: {
      freshness: null,
      analyticsQuality: { contractVersion: "broken" },
    },
  });
  await page.goto("/dashboard?page=user&roster=roster_1");

  await expect(page.locator("[data-freshness-banner]")).toContainText("更新情報を確認できません");
  await expect(page.locator('[data-module="profile"]')).toContainText("山田 太郎");
  await expect(page.locator('[data-module="summary"]')).toContainText("回答成功率");
  await expect(page.locator('[data-module="needs"]')).toContainText("情報確認");
  await expect(page.locator("#conversationList")).toContainText("製品情報の確認");
});

test("malformed chooser metadata never erases valid users", async ({ page }) => {
  await installApiMocks(page, { usersOverride: { scopeUserCount: null, freshness: null } });
  await page.goto("/dashboard?page=user");

  await expect(page.locator("[data-freshness-banner]")).toContainText("更新情報を確認できません");
  await expect(page.locator(".userChoice").filter({ hasText: "山田 太郎" })).toHaveCount(1);
});

test("inactive direct user link returns to the chooser with one clear explanation", async ({ page }) => {
  await installApiMocks(page, { detailNotFound: true });
  await page.goto("/dashboard?page=user&roster=roster_1");
  await expect(page).toHaveURL(/page=user(?!.*roster)/);
  await expect(page.locator(".chooserPanel")).toBeVisible();
  await expect(page.getByText("対象ユーザーは停用済み、または分析対象外です。ユーザー管理から確認してください。")).toBeVisible();
  await expect(page.locator('[data-module="profile"]')).toHaveCount(0);
});

test("the 80-person chooser is compact, searchable and paginated", async ({ page }) => {
  await installApiMocks(page, { usersOverride: { users: makeAnalyticsUsers(80) } });
  await page.goto("/dashboard?page=user");
  await expect(page.locator(".userChoice")).toHaveCount(15);
  await expect(page.locator(".chooserPanel")).toContainText("1–15 / 80名");
  await page.locator("#userSearch").fill("利用者 80");
  await expect(page.locator(".userChoice")).toHaveCount(1);
  await expect(page.locator(".userChoice")).toContainText("利用者 80");
});

test("selected user URL survives reload and browser back returns to the chooser", async ({ page }) => {
  await installApiMocks(page);
  await page.goto("/dashboard?page=user");
  await page.locator('.userChoice[data-roster="roster_1"]').click();
  await expect(page).toHaveURL(/page=user.*roster=roster_1/);
  await expect(page.locator('[data-module="profile"]')).toContainText("山田 太郎");

  await page.reload();
  await expect(page.locator('[data-module="profile"]')).toContainText("山田 太郎");
  await page.goBack();
  await expect(page).toHaveURL(/page=user(?!.*roster)/);
  await expect(page.locator(".chooserPanel")).toBeVisible();
  await expect(page.locator("#pageRoot")).toBeFocused();
});

test("clicking the active user navigation keeps the selected roster", async ({ page }) => {
  await installApiMocks(page);
  await page.goto("/dashboard?page=user&roster=roster_1");
  await page.getByRole("button", { name: "ユーザー分析" }).click();
  await expect(page).toHaveURL(/roster=roster_1/);
  await expect(page.locator('[data-module="profile"]')).toContainText("山田 太郎");
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
