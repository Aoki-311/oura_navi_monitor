const { test, expect } = require("@playwright/test");
const { installApiMocks, managedLabels, managedUsers } = require("./fixtures");

test("user management edits roster fields, labels and active state without scope controls", async ({ page }) => {
  const requests = []; await installApiMocks(page, { requests }); await page.goto("/dashboard?page=management&roster=roster_1");
  await expect(page.locator(".drawer")).toBeVisible();
  await expect(page.locator(".drawer")).toContainText("分析対象は部門から自動決定");
  await expect(page.locator('input[name="scope"]')).toHaveCount(0);
  await expect(page.locator('input[name="email"]')).toHaveAttribute("readonly", "");
  await page.locator('input[name="name"]').fill("山田 太郎 更新");
  await page.locator("#userForm").getByRole("button", { name: "保存" }).click();
  await expect.poll(() => requests.some((row) => row.method === "PATCH" && row.path === "/api/admin/users/roster_1")).toBeTruthy();
  const update = requests.find((row) => row.method === "PATCH" && row.path === "/api/admin/users/roster_1");
  expect(update.body).toMatchObject({ name: "山田 太郎 更新", label_ids: ["label_1"], is_active: true, expected_updated_at: "2026-08-23T01:00:00Z" });
});

test("a concurrent update keeps the drawer open with a stable inline error", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, { requests, managementUserConflict: true });
  await page.goto("/dashboard?page=management&roster=roster_1");
  await page.locator('input[name="name"]').fill("競合中の編集");
  await page.locator("#userForm").getByRole("button", { name: "保存" }).click();
  await expect(page.locator(".drawer")).toBeVisible();
  await expect(page.locator("#userFormError")).toContainText("別の管理者が先に更新しました");
  await expect(page.locator('input[name="name"]')).toHaveValue("競合中の編集");
  expect(requests.filter((row) => row.method === "PATCH" && row.path === "/api/admin/users/roster_1")).toHaveLength(1);
});

test("an inactive label already assigned to a user remains visible and retained", async ({ page }) => {
  await installApiMocks(page, {
    managedUsersOverride: {
      users: [{ ...managedUsers.users[0], labelIds: ["label_1", "label_old"] }],
    },
    managedLabelsOverride: {
      labels: [...managedLabels.labels, {
        labelId: "label_old", name: "旧分類", color: "#5f6285", usageCount: 1,
        isActive: false, updatedAt: "2026-08-22T01:00:00Z", updatedBy: "admin@example.com",
      }],
    },
  });
  await page.goto("/dashboard?page=management&roster=roster_1");
  const retained = page.locator('input[name="label"][value="label_old"]');
  await expect(retained).toBeChecked();
  await expect(retained).toBeDisabled();
  await expect(page.locator("#userForm")).toContainText("旧分類（停用・保持）");
});
