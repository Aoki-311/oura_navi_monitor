const { test, expect } = require("@playwright/test");
const { installApiMocks } = require("./fixtures");

test("label management creates a monitor-only label from fixed colors", async ({ page }) => {
  const requests = []; await installApiMocks(page, { requests }); await page.goto("/dashboard?page=management");
  await page.getByRole("tab", { name: /ラベル管理/ }).click();
  await page.getByRole("button", { name: "ラベルを追加" }).click();
  await page.locator('input[name="name"]').fill("研修対象");
  await page.locator("#labelForm").getByRole("button", { name: "保存" }).click();
  await expect.poll(() => requests.some((row) => row.method === "POST" && row.path === "/api/admin/labels" && row.body?.name === "研修対象")).toBeTruthy();
});

test("label edits carry the displayed revision", async ({ page }) => {
  const requests = []; await installApiMocks(page, { requests }); await page.goto("/dashboard?page=management");
  await page.getByRole("tab", { name: /ラベル管理/ }).click();
  await page.getByRole("button", { name: "編集" }).click();
  await page.locator('#labelForm input[name="name"]').fill("重点更新");
  await page.locator("#labelForm").getByRole("button", { name: "保存" }).click();
  await expect.poll(() => requests.some((row) => row.method === "PATCH" && row.path === "/api/admin/labels/label_1")).toBeTruthy();
  const update = requests.find((row) => row.method === "PATCH" && row.path === "/api/admin/labels/label_1");
  expect(update.body).toMatchObject({ name: "重点更新", expected_updated_at: "2026-08-23T01:00:00Z" });
});
