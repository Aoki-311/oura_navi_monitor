const { test, expect } = require("@playwright/test");
const { installApiMocks, makeManagedUsers, managedLabels, managedUsers } = require("./fixtures");

test("user management edits roster fields, labels and active state without scope controls", async ({ page }) => {
  const requests = []; await installApiMocks(page, { requests }); await page.goto("/dashboard?page=management&roster=roster_1");
  await expect(page.locator(".drawer")).toBeVisible();
  await expect(page.locator("#scopeImpact")).toContainText("全体サマリーとユーザー分析");
  await expect(page.locator(".drawer")).not.toContainText("サーバーが判定");
  await expect(page.locator('input[name="scope"]')).toHaveCount(0);
  await expect(page.locator('input[name="email"]')).toHaveAttribute("readonly", "");
  await page.locator('input[name="name"]').fill("山田 太郎 更新");
  await page.locator("#userForm").getByRole("button", { name: "保存" }).click();
  await expect.poll(() => requests.some((row) => row.method === "PATCH" && row.path === "/api/admin/users/roster_1")).toBeTruthy();
  const update = requests.find((row) => row.method === "PATCH" && row.path === "/api/admin/users/roster_1");
  expect(update.body).toMatchObject({
    name: "山田 太郎 更新",
    label_ids: ["label_1"],
    is_active: true,
    expected_updated_at: "2026-08-23T01:00:00Z",
    expected_scope_policy_version: "summary_role_v1",
  });
});

for (const repairCase of [
  { name: "empty area and invalid department", area: "", department: "旧部門" },
  { name: "invalid area and empty department", area: "旧エリア", department: "" },
]) {
  test(`existing invalid roster values require an explicit repair: ${repairCase.name}`, async ({ page }) => {
    const requests = [];
    await installApiMocks(page, {
      requests,
      managedUsersOverride: {
        users: [{
          ...managedUsers.users[0],
          area: repairCase.area,
          role: "",
          department: repairCase.department,
          rosterIssues: ["invalid_roster_value"],
        }],
      },
    });
    await page.goto("/dashboard?page=management&roster=roster_1");

    await expect(page.locator('select[name="area"]')).toHaveValue("");
    await expect(page.locator('select[name="role"]')).toHaveValue("");
    await expect(page.locator('select[name="department"]')).toHaveValue("");
    await expect(page.locator("#scopeImpact")).toContainText("要修正");
    await expect(page.locator("#scopeImpact")).toContainText("エリア・役割・部門");
    await expect(page.locator("#userForm").getByRole("button", { name: "保存" })).toBeDisabled();
    expect(requests.some((row) => row.path === "/api/admin/scope-preview")).toBeFalsy();

    await page.locator("#userForm").evaluate((form) => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    await expect(page.locator("#userFormError")).toContainText("要修正の名簿項目を選択するまで保存できません");
    expect(requests.some((row) => row.method === "PATCH" && row.path === "/api/admin/users/roster_1")).toBeFalsy();

    await page.locator('select[name="area"]').selectOption("関西");
    await page.locator('select[name="role"]').selectOption("本社MR");
    expect(requests.some((row) => row.path === "/api/admin/scope-preview")).toBeFalsy();
    await page.locator('select[name="department"]').selectOption("DM専任");
    await expect(page.locator("#scopeImpact")).toContainText("全体サマリーとユーザー分析");
    await expect(page.locator("#userForm").getByRole("button", { name: "保存" })).toBeEnabled();
  });
}

test("a mismatched save response becomes committed-unverified and never repeats the mutation", async ({ page }) => {
  let updateBody = null;
  let patchCount = 0;
  await installApiMocks(page);
  await page.route("**/api/admin/users/roster_1", async (route) => {
    if (route.request().method() !== "PATCH") return route.fallback();
    patchCount += 1;
    updateBody = route.request().postDataJSON();
    return route.fulfill({ status: 200, json: { ...managedUsers.users[0], globalScopeEnabled: false } });
  });
  await page.goto("/dashboard?page=management&roster=roster_1");
  await expect(page.locator("#scopeImpact")).toContainText("全体サマリーとユーザー分析");
  await page.locator('input[name="name"]').fill("不一致を検出");
  await page.locator("#userForm").getByRole("button", { name: "保存" }).click();

  await expect(page.locator(".drawer")).toBeVisible();
  await expect(page.locator("#userFormError")).toContainText("変更は受付済みですが、保存結果を確認できません");
  await expect(page.getByRole("button", { name: "確認を再試行" })).toBeVisible();
  await page.getByRole("button", { name: "確認を再試行" }).click();
  await expect(page.locator("#userFormError")).toContainText("保存結果を確認できません");
  expect(patchCount).toBe(1);
  expect(updateBody.expected_scope_policy_version).toBe("summary_role_v1");
});

test("role changes recompute summary membership through the server policy", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, { requests });
  await page.goto("/dashboard?page=management&roster=roster_1");
  await expect(page.locator("#scopeImpact")).toContainText("全体サマリーとユーザー分析");
  await page.locator('select[name="role"]').selectOption("本社メンバー");
  await expect(page.locator("#scopeImpact")).toContainText("ユーザー分析に含まれ、全体サマリーには含まれません");
  await expect.poll(() => requests.filter((row) => row.path === "/api/admin/scope-preview").at(-1)?.body?.role).toBe("本社メンバー");
});

test("a label catalog failure cannot erase existing label assignments on save", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, { requests, failManagedLabels: true });
  await page.goto("/dashboard?page=management&roster=roster_1");
  await expect(page.locator("#userForm fieldset")).toHaveAttribute("disabled", "");
  await expect(page.locator('#userForm input[name="label"]')).toHaveCount(0);
  await expect(page.locator("#userForm")).toContainText("現在の関係を変更せず保存");
  await page.locator('input[name="name"]').fill("ラベル保持更新");
  await page.locator("#userForm").getByRole("button", { name: "保存" }).click();
  await expect.poll(() => requests.some((row) => row.method === "PATCH" && row.path === "/api/admin/users/roster_1")).toBeTruthy();
  const update = requests.find((row) => row.method === "PATCH" && row.path === "/api/admin/users/roster_1");
  expect(update.body.name).toBe("ラベル保持更新");
  expect(update.body).not.toHaveProperty("label_ids");
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

test("dirty user edits guard navigation, refresh, hidden date controls, history, and browser unload", async ({ page }) => {
  await installApiMocks(page);
  await page.goto("/dashboard");
  await page.getByRole("button", { name: "ユーザー管理" }).click();
  await page.locator('[data-edit-user="roster_1"]').first().click();
  const name = page.locator('input[name="name"]');
  await name.fill("破棄していない編集");
  await page.evaluate(() => {
    window.__discardPrompts = [];
    window.confirm = (message) => { window.__discardPrompts.push(String(message)); return false; };
  });

  const dismissDiscard = async (action) => {
    const before = await page.evaluate(() => window.__discardPrompts.length);
    await action();
    await expect.poll(() => page.evaluate(() => window.__discardPrompts.length)).toBe(before + 1);
    const messages = await page.evaluate(() => window.__discardPrompts);
    expect(messages.at(-1)).toContain("保存していない変更");
  };

  await dismissDiscard(() => page.evaluate(() => document.querySelector('.mainNav [data-page="overview"]').click()));
  await expect(page).toHaveURL(/page=management/);
  await expect(name).toHaveValue("破棄していない編集");

  await dismissDiscard(() => page.evaluate(() => document.querySelector("#managementRefreshButton").click()));
  await expect(name).toHaveValue("破棄していない編集");

  await expect(page.locator("#mainDateRange")).toBeHidden();
  await expect(name).toHaveValue("破棄していない編集");

  const unload = await page.evaluate(() => {
    const event = new Event("beforeunload", { cancelable: true });
    const dispatched = window.dispatchEvent(event);
    return { dispatched, defaultPrevented: event.defaultPrevented };
  });
  expect(unload).toEqual({ dispatched: false, defaultPrevented: true });

  await dismissDiscard(() => page.evaluate(() => window.history.back()));
  await expect(page).toHaveURL(/page=management/);
  await expect(name).toHaveValue("破棄していない編集");
});

test("a user save is a hard leave barrier until its verified response and readback return", async ({ page }) => {
  let releasePatch;
  let markPatchStarted;
  let releaseReadback;
  let markReadbackStarted;
  let patchCommitted = false;
  const patchStarted = new Promise((resolve) => { markPatchStarted = resolve; });
  const patchReleased = new Promise((resolve) => { releasePatch = resolve; });
  const readbackStarted = new Promise((resolve) => { markReadbackStarted = resolve; });
  const readbackReleased = new Promise((resolve) => { releaseReadback = resolve; });
  await installApiMocks(page);
  await page.route("**/api/admin/users/roster_1", async (route) => {
    if (route.request().method() !== "PATCH") return route.fallback();
    markPatchStarted();
    await patchReleased;
    patchCommitted = true;
    return route.fulfill({ status: 200, json: {
      ...managedUsers.users[0],
      name: "保存処理中",
      updatedAt: "2026-08-24T01:00:01Z",
    } });
  });
  await page.route(/\/api\/admin\/users(?:\?.*)?$/, async (route) => {
    if (route.request().method() !== "GET" || !patchCommitted) return route.fallback();
    markReadbackStarted();
    await readbackReleased;
    return route.fulfill({ status: 200, json: { users: [{
      ...managedUsers.users[0],
      name: "保存処理中",
      updatedAt: "2026-08-24T01:00:01Z",
    }] } });
  });
  await page.goto("/dashboard?page=management&roster=roster_1");
  await page.locator('input[name="name"]').fill("保存処理中");
  await page.locator("#userForm").getByRole("button", { name: "保存" }).click();
  await patchStarted;
  await expect(page.locator("#userForm")).toHaveAttribute("aria-busy", "true");

  await page.evaluate(() => {
    window.__unexpectedPrompts = [];
    window.confirm = (message) => { window.__unexpectedPrompts.push(String(message)); return false; };
  });
  await page.evaluate(() => document.querySelector('.mainNav [data-page="overview"]').click());
  await expect(page).toHaveURL(/page=management/);
  await expect(page.locator(".drawer")).toBeVisible();
  await expect(page.locator("#toast")).toContainText("保存結果を確認中");
  expect(await page.evaluate(() => window.__unexpectedPrompts)).toEqual([]);

  releasePatch();
  await readbackStarted;
  await page.evaluate(() => document.querySelector('.mainNav [data-page="overview"]').click());
  await expect(page).toHaveURL(/page=management/);
  await expect(page.locator("#toast")).toContainText("保存結果を確認中");

  releaseReadback();
  await expect(page.locator(".drawer")).toHaveCount(0);
  await page.getByRole("button", { name: "全体サマリー" }).click();
  await expect(page).toHaveURL((url) => url.pathname === "/dashboard" && !url.searchParams.has("page") && url.searchParams.has("start") && url.searchParams.has("end"));
});

test("a committed user write with a failed readback can only retry the GET verification", async ({ page }) => {
  let patchCount = 0;
  let readbackCount = 0;
  let committed = false;
  const savedUser = {
    ...managedUsers.users[0],
    name: "読込確認待ち",
    updatedAt: "2026-08-24T01:00:02Z",
  };
  await installApiMocks(page);
  await page.route("**/api/admin/users/roster_1", async (route) => {
    if (route.request().method() !== "PATCH") return route.fallback();
    patchCount += 1;
    committed = true;
    return route.fulfill({ status: 200, json: savedUser });
  });
  await page.route(/\/api\/admin\/users(?:\?.*)?$/, async (route) => {
    if (route.request().method() !== "GET" || !committed) return route.fallback();
    readbackCount += 1;
    if (readbackCount === 1) {
      return route.fulfill({ status: 503, json: { detail: { code: "source_unavailable", message: "readback unavailable" } } });
    }
    return route.fulfill({ status: 200, json: { users: [savedUser] } });
  });
  await page.goto("/dashboard?page=management&roster=roster_1");
  await page.locator('input[name="name"]').fill("読込確認待ち");
  await page.locator("#userForm").getByRole("button", { name: "保存" }).click();

  await expect(page.locator("#userFormError")).toContainText("変更は受付済みですが、保存結果を確認できません");
  await expect(page.getByRole("button", { name: "確認を再試行" })).toBeEnabled();
  await page.evaluate(() => document.querySelector('.mainNav [data-page="overview"]').click());
  await expect(page).toHaveURL(/page=management/);
  expect(patchCount).toBe(1);
  await page.getByRole("button", { name: "確認を再試行" }).click();
  await expect(page.locator(".drawer")).toHaveCount(0);
  await expect(page.locator("#toast")).toContainText("ユーザー情報を保存しました");
  expect(patchCount).toBe(1);
  expect(readbackCount).toBe(2);
});

test("a readback_conflict response is treated as committed and can only retry canonical GET", async ({ page }) => {
  let patchCount = 0;
  let readbackCount = 0;
  let committed = false;
  const savedUser = {
    ...managedUsers.users[0],
    name: "409確認待ち",
    updatedAt: "2026-08-24T01:00:03Z",
  };
  await installApiMocks(page);
  await page.route("**/api/admin/users/roster_1", async (route) => {
    if (route.request().method() !== "PATCH") return route.fallback();
    patchCount += 1;
    committed = true;
    return route.fulfill({
      status: 409,
      json: { detail: { code: "readback_conflict", message: "write committed but readback failed" } },
    });
  });
  await page.route(/\/api\/admin\/users(?:\?.*)?$/, async (route) => {
    if (route.request().method() !== "GET" || !committed) return route.fallback();
    readbackCount += 1;
    if (readbackCount === 1) {
      return route.fulfill({
        status: 503,
        json: { detail: { code: "source_unavailable", message: "canonical readback unavailable" } },
      });
    }
    return route.fulfill({ status: 200, json: { users: [savedUser] } });
  });
  await page.goto("/dashboard?page=management&roster=roster_1");
  await page.locator('input[name="name"]').fill("409確認待ち");
  await page.locator("#userForm").getByRole("button", { name: "保存" }).click();

  await expect(page.locator("#userFormError")).toContainText("変更は受付済みですが、保存結果を確認できません");
  await expect(page.getByRole("button", { name: "確認を再試行" })).toBeEnabled();
  await page.locator("#userForm").evaluate((form) => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  expect(patchCount).toBe(1);
  await page.getByRole("button", { name: "確認を再試行" }).click();
  await expect(page.locator(".drawer")).toHaveCount(0);
  await expect(page.locator("#toast")).toContainText("ユーザー情報を保存しました");
  expect(patchCount).toBe(1);
  expect(readbackCount).toBe(2);
});

test("a normalized duplicate label can be repaired by renaming one exact row while unsafe catalog edits stay locked", async ({ page }) => {
  const safeLabel = managedLabels.labels[0];
  const duplicateA = {
    labelId: "label_dup_a", name: " ＴＥＳＴ ", color: "#386dff", usageCount: 0,
    isActive: true, labelIssues: ["duplicate_label_name"], updatedAt: "2026-08-23T01:00:00Z", updatedBy: "admin@example.com",
  };
  const duplicateB = {
    labelId: "label_dup_b", name: "test", color: "#ffb340", usageCount: 0,
    isActive: true, labelIssues: ["duplicate_label_name"], updatedAt: "2026-08-23T01:00:00Z", updatedBy: "admin@example.com",
  };
  const repairedA = {
    ...duplicateA,
    name: "重点フォロー",
    labelIssues: [],
    updatedAt: "2026-08-24T01:00:04Z",
  };
  const repairedB = { ...duplicateB, labelIssues: [] };
  let repaired = false;
  let patchCount = 0;
  let patchBody = null;
  await installApiMocks(page);
  await page.route(/\/api\/admin\/labels(?:\?.*)?$/, async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    return route.fulfill({
      status: 200,
      json: { labels: repaired ? [safeLabel, repairedA, repairedB] : [safeLabel, duplicateA, duplicateB] },
    });
  });
  await page.route("**/api/admin/labels/label_dup_a", async (route) => {
    if (route.request().method() !== "PATCH") return route.fallback();
    patchCount += 1;
    patchBody = route.request().postDataJSON();
    repaired = true;
    return route.fulfill({ status: 200, json: repairedA });
  });
  await page.goto("/dashboard?page=management&management_tab=labels");

  await page.getByRole("tab", { name: /ユーザー管理/ }).click();
  await page.locator('[data-edit-user="roster_1"]').first().click();
  await expect(page.locator("#userForm fieldset")).toHaveAttribute("disabled", "");
  await expect(page.locator("#userForm")).toContainText("現在の関係を変更せず保存");
  await page.getByRole("button", { name: "キャンセル" }).click();
  await page.getByRole("tab", { name: /ラベル管理/ }).click();
  await expect(page.getByRole("button", { name: "ラベルを追加" })).toBeDisabled();
  await expect(page.locator('[data-edit-label="label_1"]')).toBeDisabled();
  await expect(page.locator('[data-edit-label="label_dup_a"]')).toBeEnabled();
  await expect(page.locator('[data-edit-label="label_dup_a"]')).toHaveText("名称を修復");
  await page.locator('[data-edit-label="label_dup_a"]').click();
  await expect(page.locator("#labelForm fieldset")).toHaveAttribute("disabled", "");
  await expect(page.getByRole("button", { name: "削除" })).toBeDisabled();
  await page.locator('#labelForm input[name="name"]').fill("重点フォロー");
  await page.locator("#labelForm").getByRole("button", { name: "名称を修復" }).click();

  await expect(page.locator(".drawer")).toHaveCount(0);
  await expect(page.locator("#managementPanel")).toContainText("重点フォロー");
  await expect(page.getByRole("button", { name: "ラベルを追加" })).toBeEnabled();
  expect(patchCount).toBe(1);
  expect(patchBody).toMatchObject({
    name: "重点フォロー",
    color: duplicateA.color,
    is_active: duplicateA.isActive,
    expected_updated_at: duplicateA.updatedAt,
  });
});

test("an in-flight label delete blocks leaving and a failed delete is shown inline", async ({ page }) => {
  const unusedLabel = {
    labelId: "label_free", name: "未使用", color: "#5f6285", usageCount: 0,
    isActive: true, labelIssues: [], updatedAt: "2026-08-23T01:00:00Z", updatedBy: "admin@example.com",
  };
  let releaseDelete;
  let markDeleteStarted;
  const deleteStarted = new Promise((resolve) => { markDeleteStarted = resolve; });
  const deleteReleased = new Promise((resolve) => { releaseDelete = resolve; });
  await installApiMocks(page, { managedLabelsOverride: { labels: [...managedLabels.labels, unusedLabel] } });
  await page.route("**/api/admin/labels/label_free", async (route) => {
    if (route.request().method() !== "DELETE") return route.fallback();
    markDeleteStarted();
    await deleteReleased;
    return route.fulfill({ status: 409, json: { detail: { code: "conflict", message: "削除結果を確認できませんでした" } } });
  });
  await page.goto("/dashboard?page=management&management_tab=labels");
  await page.locator('[data-edit-label="label_free"]').click();
  await page.evaluate(() => {
    window.__unexpectedPrompts = [];
    window.confirm = (message) => {
      if (String(message).includes("未使用ラベルを削除")) return true;
      window.__unexpectedPrompts.push(String(message));
      return false;
    };
  });
  await page.getByRole("button", { name: "削除" }).click();
  await deleteStarted;

  await page.evaluate(() => document.querySelector('.mainNav [data-page="overview"]').click());
  await expect(page).toHaveURL(/page=management/);
  await expect(page.locator("#toast")).toContainText("削除結果を確認中");
  expect(await page.evaluate(() => window.__unexpectedPrompts)).toEqual([]);

  releaseDelete();
  await expect(page.locator("#labelFormError")).toContainText("削除結果を確認できませんでした");
  await expect(page.locator(".drawer")).toBeVisible();
});

test("a successful label delete stays leave-blocked through its catalog readback", async ({ page }) => {
  const unusedLabel = {
    labelId: "label_free", name: "未使用", color: "#5f6285", usageCount: 0,
    isActive: true, labelIssues: [], updatedAt: "2026-08-23T01:00:00Z", updatedBy: "admin@example.com",
  };
  let deleteCommitted = false;
  let releaseDelete;
  let markDeleteStarted;
  let releaseReadback;
  let markReadbackStarted;
  const deleteStarted = new Promise((resolve) => { markDeleteStarted = resolve; });
  const deleteReleased = new Promise((resolve) => { releaseDelete = resolve; });
  const readbackStarted = new Promise((resolve) => { markReadbackStarted = resolve; });
  const readbackReleased = new Promise((resolve) => { releaseReadback = resolve; });
  await installApiMocks(page, { managedLabelsOverride: { labels: [...managedLabels.labels, unusedLabel] } });
  await page.route("**/api/admin/labels/label_free", async (route) => {
    if (route.request().method() !== "DELETE") return route.fallback();
    markDeleteStarted();
    await deleteReleased;
    deleteCommitted = true;
    return route.fulfill({ status: 204, body: "" });
  });
  await page.route(/\/api\/admin\/labels(?:\?.*)?$/, async (route) => {
    if (route.request().method() !== "GET" || !deleteCommitted) return route.fallback();
    markReadbackStarted();
    await readbackReleased;
    return route.fulfill({ status: 200, json: managedLabels });
  });
  await page.goto("/dashboard?page=management&management_tab=labels");
  await page.locator('[data-edit-label="label_free"]').click();
  await page.evaluate(() => {
    window.confirm = (message) => String(message).includes("未使用ラベルを削除");
  });
  await page.getByRole("button", { name: "削除" }).click();
  await deleteStarted;

  await page.evaluate(() => document.querySelector('.mainNav [data-page="overview"]').click());
  await expect(page).toHaveURL(/page=management/);
  await expect(page.locator("#toast")).toContainText("削除結果を確認中");

  releaseDelete();
  await readbackStarted;
  await page.evaluate(() => document.querySelector('.mainNav [data-page="overview"]').click());
  await expect(page).toHaveURL(/page=management/);
  await expect(page.locator("#toast")).toContainText("変更は受付済み");

  releaseReadback();
  await expect(page.locator(".drawer")).toHaveCount(0);
  await expect(page.locator("#toast")).toContainText("ラベルを削除しました");
  await page.getByRole("button", { name: "全体サマリー" }).click();
  await expect(page.locator(".overviewHeading h2")).toHaveText("全体サマリー");
  await expect.poll(() => new URL(page.url()).searchParams.get("page")).toBeNull();
});

test("an inactive label already assigned to a user remains visible and retained", async ({ page }) => {
  await installApiMocks(page, {
    managedUsersOverride: {
      users: [{ ...managedUsers.users[0], labelIds: ["label_1", "label_old"] }],
    },
    managedLabelsOverride: {
      labels: [...managedLabels.labels, {
        labelId: "label_old", name: "旧分類", color: "#5f6285", usageCount: 1,
        isActive: false, labelIssues: [], updatedAt: "2026-08-22T01:00:00Z", updatedBy: "admin@example.com",
      }],
    },
  });
  await page.goto("/dashboard?page=management&roster=roster_1");
  const retained = page.locator('input[name="label"][value="label_old"]');
  await expect(retained).toBeChecked();
  await expect(retained).toBeDisabled();
  await expect(page.locator("#userForm")).toContainText("旧分類（停用・保持）");
});

test("saving repairs dangling labels while preserving known inactive and selected active labels", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, {
    requests,
    managedUsersOverride: {
      users: [{
        ...managedUsers.users[0],
        labelIds: ["label_1", "label_old", "label_missing"],
      }],
    },
    managedLabelsOverride: {
      labels: [
        ...managedLabels.labels,
        {
          labelId: "label_new", name: "追加分類", color: "#386dff", usageCount: 0,
          isActive: true, labelIssues: [], updatedAt: "2026-08-22T00:00:00Z", updatedBy: "admin@example.com",
        },
        {
          labelId: "label_old", name: "旧分類", color: "#5f6285", usageCount: 1,
          isActive: false, labelIssues: [], updatedAt: "2026-08-22T01:00:00Z", updatedBy: "admin@example.com",
        },
      ],
    },
  });
  await page.goto("/dashboard?page=management&roster=roster_1");

  await expect(page.locator("#userForm")).toContainText("label_missing");
  await expect(page.locator('input[name="label"][value="label_old"]')).toBeDisabled();
  await page.locator('input[name="label"][value="label_new"]').check();
  await page.locator('input[name="name"]').fill("ラベル関係を修復");
  await page.locator("#userForm").getByRole("button", { name: "保存" }).click();

  await expect.poll(() => requests.find(
    (row) => row.method === "PATCH" && row.path === "/api/admin/users/roster_1",
  )?.body?.label_ids).toEqual(["label_1", "label_new", "label_old"]);
  await expect(page.locator(".drawer")).toHaveCount(0);
});

test("frontend canonical text and email folding match the backend write owner", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, {
    requests,
    managedUsersOverride: {
      users: [{ ...managedUsers.users[0], identityBound: false }],
    },
  });
  await page.goto("/dashboard?page=management&roster=roster_1");

  const canonical = await page.evaluate(async () => {
    const module = await import("/dashboard-assets/contracts/canonicalText.js");
    return {
      text: module.normalizeCanonicalText("\u3000Ａ\u001c\u001cＢ\u3000"),
      email: module.normalizeCanonicalEmail("\u3000ＳＴＲＡẞＥ＠ＥＸＡＭＰＬＥ．ＣＯＭ\u3000"),
    };
  });
  expect(canonical).toEqual({ text: "A B", email: "strasse@example.com" });

  await page.locator('input[name="name"]').fill("\u3000山田\u3000\u3000太郎\u3000");
  await page.locator('input[name="email"]').fill("USER1@EXAMPLE.COM");
  await page.locator("#userForm").getByRole("button", { name: "保存" }).click();
  await expect.poll(() => requests.find(
    (row) => row.method === "PATCH" && row.path === "/api/admin/users/roster_1",
  )?.body?.email).toBe("USER1@EXAMPLE.COM");
  await expect(page.locator(".drawer")).toHaveCount(0);
});

test("rolling old management rows remain visible while unverified scope edits stay disabled", async ({ page }) => {
  const legacyUser = { ...managedUsers.users[0] };
  delete legacyUser.scopePolicyVersion;
  delete legacyUser.rosterIssues;
  await installApiMocks(page, {
    managedUsersOverride: { users: [legacyUser] },
    managementMetadataOverride: { summaryRoles: null, scopePolicyVersion: null },
  });
  await page.goto("/dashboard?page=management");

  await expect(page.locator("#managementPanel")).toContainText("山田 太郎");
  await expect(page.locator("#managementPanel")).toContainText("対象判定未確認");
  await expect(page.locator("#managementPanel")).toContainText("旧形式のため分析対象を再確認してください");
  const editButtons = page.locator('[data-edit-user="roster_1"]');
  await expect(editButtons).toHaveCount(2);
  expect(await editButtons.evaluateAll((buttons) => buttons.every((button) => button.disabled))).toBeTruthy();
});

test("a failed server scope preview cannot save an unverified roster change", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, { requests, failScopePreview: true });
  await page.goto("/dashboard?page=management&roster=roster_1");

  await expect(page.locator("#scopeImpact")).toContainText("データを読み込めませんでした");
  await expect(page.locator("#userForm").getByRole("button", { name: "保存" })).toBeDisabled();
  await page.locator("#userForm").evaluate((form) => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  await expect(page.locator("#userFormError")).toContainText("分析対象の確認が完了していないため保存できません");
  expect(requests.some((row) => row.method === "PATCH" && row.path === "/api/admin/users/roster_1")).toBeFalsy();
});

test("the 83-person management table keeps search focus and paginates", async ({ page }) => {
  await installApiMocks(page, { managedUsersOverride: { users: makeManagedUsers(83) } });
  await page.goto("/dashboard?page=management");
  await expect(page.locator("#managementPanel tbody tr")).toHaveCount(20);
  const search = page.locator("#userSearch");
  await search.focus();
  await search.pressSequentially("利用者 83");
  await expect(search).toBeFocused();
  await expect(search).toHaveValue("利用者 83");
  await expect(page.locator("#managementPanel tbody tr")).toHaveCount(1);
  await expect(page.locator("#managementPanel tbody")).toContainText("利用者 83");
});
