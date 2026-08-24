const { test, expect } = require("@playwright/test");
const { installApiMocks } = require("./fixtures");

test("label management creates a monitor-only label from fixed colors", async ({ page }) => {
  const requests = []; await installApiMocks(page, { requests }); await page.goto("/dashboard?page=management");
  await page.getByRole("button", { name: /ラベル管理/ }).click();
  await page.getByRole("button", { name: "ラベルを追加" }).click();
  await page.locator('input[name="name"]').fill("研修対象");
  await page.locator("#labelForm").getByRole("button", { name: "保存" }).click();
  await expect.poll(() => requests.some((row) => row.method === "POST" && row.path === "/api/admin/labels" && row.body?.name === "研修対象")).toBeTruthy();
});
