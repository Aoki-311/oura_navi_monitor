const { test, expect } = require("@playwright/test");
const { installApiMocks } = require("./fixtures");

test("user management edits roster fields, labels and active state without scope controls", async ({ page }) => {
  const requests = []; await installApiMocks(page, { requests }); await page.goto("/dashboard?page=management&roster=roster_1");
  await expect(page.locator(".drawer")).toBeVisible();
  await expect(page.locator(".drawer")).toContainText("分析範囲は部門から自動決定");
  await expect(page.locator('input[name="scope"]')).toHaveCount(0);
  await page.locator('input[name="name"]').fill("山田 太郎 更新");
  await page.locator("#userForm").getByRole("button", { name: "保存" }).click();
  await expect.poll(() => requests.some((row) => row.method === "PATCH" && row.path === "/api/admin/users/roster_1")).toBeTruthy();
  const update = requests.find((row) => row.method === "PATCH" && row.path === "/api/admin/users/roster_1");
  expect(update.body).toMatchObject({ name: "山田 太郎 更新", label_ids: ["label_1"], is_active: true });
});
