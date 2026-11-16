const { test, expect } = require("@playwright/test");
const { installApiMocks, managedUsers, makeManagedUsers, managementMetadata } = require("./fixtures");

async function installDeletionApi(page, users = managedUsers.users) {
  await installApiMocks(page);
  const state = {
    users: structuredClone(users),
    metadata: structuredClone(managementMetadata),
    deletions: [],
    reads: [],
    failReadback: false,
    exposeDeleted: false,
    onDelete: null,
    archive(id) {
      const user = this.users.find((row) => row.rosterId === id);
      Object.assign(user, {
        isDeleted: true, isActive: false, globalScopeEnabled: false, userMapScopeEnabled: false,
        deletedAt: "2026-11-17T01:00:00Z", updatedAt: "2026-11-17T01:00:00Z",
      });
      return user;
    },
  };
  await page.route(/\/api\/admin\/metadata$/, (route) => route.fulfill({ json: state.metadata }));
  await page.route(/\/api\/admin\/users(?:\?.*)?$/, async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    const includeDeleted = new URL(route.request().url()).searchParams.get("include_deleted") === "true";
    state.reads.push(includeDeleted);
    if (state.failReadback && state.deletions.length) return route.fulfill({ status: 503, json: { detail: "unavailable" } });
    return route.fulfill({ json: { users: state.users.filter((row) => includeDeleted || state.exposeDeleted || !row.isDeleted) } });
  });
  await page.route(/\/api\/admin\/users\/[^/?]+$/, async (route) => {
    if (route.request().method() !== "DELETE") return route.fallback();
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split("/").at(-1));
    state.deletions.push({ id, body: route.request().postDataJSON() });
    if (state.onDelete) return state.onDelete(route, id);
    return route.fulfill({ json: state.archive(id) });
  });
  return state;
}

async function openDeletion(page, rosterId = "roster_1") {
  await page.goto(`/dashboard?page=management&roster=${rosterId}`);
  await page.getByRole("button", { name: "ユーザーを削除", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "ユーザーを削除しますか？" })).toBeVisible();
}

test("deletion clearly identifies the person, preserves inactive users and cancels without a request", async ({ page }) => {
  const users = makeManagedUsers(10).map((row, index) => ({ ...row, isActive: index >= 7 }));
  const state = await installDeletionApi(page, users);
  await openDeletion(page);
  await expect(page.locator(".deleteUserIdentity")).toHaveText("利用者 01user1@example.com");
  await expect(page.locator("#deleteUserDescription")).toContainText("過去の会話・利用ログは保持");
  await expect(page.locator("#cancelDeleteUser")).toBeFocused();
  if (process.env.MONITOR_DELETE_SCREENSHOT) await page.screenshot({ path: process.env.MONITOR_DELETE_SCREENSHOT, fullPage: true });
  await page.locator("#cancelDeleteUser").click();
  expect(state.deletions).toHaveLength(0);
  await expect(page.locator(".managementScopeSummary")).toContainText("有効 3名");
  await expect(page.locator(".managementScopeSummary")).toContainText("停用 7名");
  await page.locator("#userStatus").selectOption("inactive");
  await expect(page.locator(".desktopTable tbody tr")).toHaveCount(7);
});

test("verified deletion preserves the current filter and page", async ({ page }) => {
  const state = await installDeletionApi(page, makeManagedUsers(45));
  await page.goto("/dashboard?page=management&management_q=利用者&management_page=2");
  await page.locator('[data-edit-user="roster_21"]').first().click();
  await page.getByRole("button", { name: "ユーザーを削除", exact: true }).click();
  await page.locator("#confirmDeleteUser").click();
  await expect(page.locator("#deleteUserForm")).toHaveCount(0);
  await expect(page.locator("#toast")).toContainText("ユーザーを削除しました");
  await expect(page).toHaveURL(/management_page=2/);
  await expect(page.locator("#userSearch")).toHaveValue("利用者");
  await expect(page.locator('[data-edit-user="roster_21"]')).toHaveCount(0);
  await expect(page.locator(".managementScopeSummary")).toContainText("名簿 44名");
  expect(state.deletions).toEqual([{
    id: "roster_21",
    body: { expected_updated_at: "2026-08-23T01:00:00Z", expected_scope_policy_version: "summary_department_v2" },
  }]);
  expect(state.reads.slice(-2)).toEqual([true, false]);
});

test("deleting is a leave barrier and repeated submission cannot issue another DELETE", async ({ page }) => {
  const state = await installDeletionApi(page);
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  state.onDelete = async (route, id) => {
    await pending;
    return route.fulfill({ json: state.archive(id) });
  };
  await openDeletion(page);
  await page.locator("#confirmDeleteUser").click();
  await expect.poll(() => state.deletions.length).toBe(1);
  await expect(page.locator("#confirmDeleteUser")).toBeDisabled();
  await page.locator("#deleteUserForm").evaluate((form) => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  await page.locator("#cancelDeleteUser").click();
  await expect(page.locator("#deleteUserForm")).toBeVisible();
  await expect(page.locator("#toast")).toContainText("削除結果を確認中");
  expect(state.deletions).toHaveLength(1);
  release();
  await expect(page.locator("#deleteUserForm")).toHaveCount(0);
});

for (const failure of ["readback unavailable", "deleted user remains in default list"]) {
  test(`${failure} cannot report success or repeat DELETE`, async ({ page }) => {
    const state = await installDeletionApi(page);
    if (failure === "readback unavailable") state.failReadback = true;
    else state.exposeDeleted = true;
    await openDeletion(page);
    await page.locator("#confirmDeleteUser").click();
    await expect(page.locator("#deleteUserError")).toContainText("削除結果を確認できません");
    await expect(page.locator("#confirmDeleteUser")).toBeDisabled();
    await expect(page.locator("#toast")).not.toContainText("削除しました");
    await page.getByRole("button", { name: "確認を再試行" }).click();
    await expect(page.locator("#deleteUserError")).toContainText("削除結果を確認できません");
    expect(state.deletions).toHaveLength(1);
    state.failReadback = false;
    state.exposeDeleted = false;
    await page.getByRole("button", { name: "確認を再試行" }).click();
    await expect(page.locator("#deleteUserForm")).toHaveCount(0);
    expect(state.deletions).toHaveLength(1);
  });
}

test("permission failure keeps the user and allows cancellation", async ({ page }) => {
  const state = await installDeletionApi(page);
  state.onDelete = (route) => route.fulfill({ status: 403, json: { detail: "forbidden" } });
  await openDeletion(page);
  await page.locator("#confirmDeleteUser").click();
  await expect(page.locator("#deleteUserError")).toContainText("アクセス権限");
  await page.locator("#cancelDeleteUser").click();
  await expect(page.locator('[data-edit-user="roster_1"]').first()).toBeVisible();
  expect(state.users[0].isDeleted).not.toBe(true);
});

for (const code of ["update_conflict", "scope_policy_conflict"]) {
  test(`${code} reloads the current identity and requires a fresh confirmation`, async ({ page }) => {
    const state = await installDeletionApi(page);
    state.onDelete = (route, id) => {
      if (state.deletions.length === 1) {
        Object.assign(state.users[0], { name: "更新後の氏名", updatedAt: "2026-11-17T00:59:00Z" });
        if (code === "scope_policy_conflict") state.metadata.scopePolicyVersion = "summary_department_next";
        return route.fulfill({ status: 409, json: { detail: { code } } });
      }
      return route.fulfill({ json: state.archive(id) });
    };
    await openDeletion(page);
    await page.locator("#confirmDeleteUser").click();
    await expect(page.locator(".deleteUserIdentity")).toContainText("更新後の氏名");
    await expect(page.locator("#deleteUserError")).toContainText("もう一度操作");
    expect(state.deletions).toHaveLength(1);
    await page.locator("#confirmDeleteUser").click();
    await expect(page.locator("#deleteUserForm")).toHaveCount(0);
    expect(state.deletions[1].body).toEqual({
      expected_updated_at: "2026-11-17T00:59:00Z", expected_scope_policy_version: state.metadata.scopePolicyVersion,
    });
  });
}

test("already deleted responses reconcile the server state without another mutation", async ({ page }) => {
  const state = await installDeletionApi(page);
  state.onDelete = (route, id) => {
    state.archive(id);
    return route.fulfill({ status: 409, json: { detail: { code: "user_deleted" } } });
  };
  await openDeletion(page);
  await page.locator("#confirmDeleteUser").click();
  await expect(page.locator("#deleteUserForm")).toHaveCount(0);
  await expect(page.locator("#toast")).toContainText("既に削除されています");
  expect(state.deletions).toHaveLength(1);
});

test("an uncertain failure verifies the archive instead of repeating the destructive request", async ({ page }) => {
  const state = await installDeletionApi(page);
  state.onDelete = (route, id) => {
    state.archive(id);
    return route.fulfill({ status: 503, json: { detail: "response interrupted" } });
  };
  await openDeletion(page);
  await page.locator("#confirmDeleteUser").click();
  await expect(page.locator("#deleteUserForm")).toHaveCount(0);
  expect(state.deletions).toHaveLength(1);
  expect(state.reads.slice(-2)).toEqual([true, false]);
});

test("an uncommitted failure permits a verified return to the list without retrying DELETE", async ({ page }) => {
  const state = await installDeletionApi(page);
  state.onDelete = (route) => route.fulfill({ status: 503, json: { detail: "failed before commit" } });
  await openDeletion(page);
  await page.locator("#confirmDeleteUser").click();
  await expect(page.locator("#deleteUserError")).toContainText("対象ユーザーは削除されていません");
  await expect(page.locator("#confirmDeleteUser")).toBeDisabled();
  await page.getByRole("button", { name: "最新の一覧に戻る" }).click();
  await expect(page.locator("#deleteUserForm")).toHaveCount(0);
  await expect(page.locator('[data-edit-user="roster_1"]').first()).toBeVisible();
  await expect(page.locator("#toast")).toContainText("削除完了は確認できていません");
  expect(state.deletions).toHaveLength(1);
  expect(state.users[0].isDeleted).not.toBe(true);
});

test("new department form choices retain block MR experience and use the server preview", async ({ page }) => {
  const state = await installDeletionApi(page);
  state.metadata.departments.push("MR(ブロック)", "MisW本社", "セールスマーケ本社");
  await page.route("**/api/admin/scope-preview", (route) => {
    const body = route.request().postDataJSON();
    return route.fulfill({ json: {
      globalScopeEnabled: body.is_active && ["MR(DM)", "MR(HCS)", "MR(ブロック)"].includes(body.department),
      userMapScopeEnabled: body.is_active && body.department !== "管理者",
      scopePolicyVersion: state.metadata.scopePolicyVersion,
    } });
  });
  await page.goto("/dashboard?page=management&roster=roster_1");
  await page.locator('select[name="department"]').selectOption("MR(ブロック)");
  await expect(page.locator('input[name="mr_experience"]')).toBeEnabled();
  await expect(page.locator('input[name="mr_experience"]')).toHaveValue("10年");
  await expect(page.locator("#scopeImpact")).toContainText("全体サマリーとユーザー分析");
  for (const department of ["MisW本社", "セールスマーケ本社"]) {
    await page.locator('select[name="department"]').selectOption(department);
    await expect(page.locator('input[name="mr_experience"]')).toBeDisabled();
    await expect(page.locator("#scopeImpact")).toContainText("全体サマリーには含まれません");
  }
});
