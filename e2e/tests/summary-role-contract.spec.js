const { test, expect } = require("@playwright/test");
const {
  detail,
  installApiMocks,
  managedUsers,
  overviewUsers,
} = require("./fixtures");

function analyticsUser({ rosterId, name, role }) {
  return {
    ...overviewUsers.users[0],
    rosterId,
    name,
    email: `${rosterId}@example.com`,
    role,
  };
}

test("summary isolates non-exact roles, keeps valid rows, and disables CSV", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, {
    requests,
    usersOverride: {
      users: [
        analyticsUser({ rosterId: "summary_mr", name: "Summary MR", role: "本社MR" }),
        analyticsUser({ rosterId: "summary_contract", name: "Summary Contract", role: "コントラクトMR" }),
        analyticsUser({ rosterId: "summary_member", name: "Summary Member", role: "本社メンバー" }),
        analyticsUser({ rosterId: "summary_unknown", name: "Summary Unknown", role: "未知の役割" }),
      ],
    },
  });

  await page.goto("/dashboard");

  await expect(page.locator("#overviewUsers tbody tr")).toHaveCount(2);
  await expect(page.locator("#overviewUserResults")).toContainText("Summary MR");
  await expect(page.locator("#overviewUserResults")).toContainText("Summary Contract");
  await expect(page.locator("#overviewUserResults")).not.toContainText("Summary Member");
  await expect(page.locator("#overviewUserResults")).not.toContainText("Summary Unknown");
  await expect(page.locator("[data-content-diagnostics]")).toHaveCount(0);
  const modelState = await page.evaluate(async () => {
    const { usersModel } = await import("/dashboard-assets/adapters/usersAdapter.js");
    return usersModel({
      scope: "global",
      scopePolicyVersion: "summary_role_v1",
      rosterFingerprint: "roster-fingerprint",
      contentFingerprint: "content-fingerprint",
      publishedRunId: "run-1",
      windowStart: "2026-08-16T15:00:00Z",
      windowEnd: "2026-08-23T01:00:00Z",
      windowTimezone: "Asia/Tokyo",
      scopeUserCount: 2,
      contentDiagnostics: { state: "complete", labelCatalogStatus: "available", issues: [] },
      freshness: { state: "fresh", dataThrough: "2026-08-23T01:00:00Z" },
      users: [
        {
          rosterId: "bad-role",
          name: "Bad Role",
          email: "bad-role@example.com",
          role: "本社メンバー",
          department: "DM専任",
          workplace: "大阪",
          area: "関西",
          areaKey: "関西",
          labels: [],
          lastActiveAt: "",
          activeDays7: 0,
          userMessageCount7: 0,
          completeDelivery: { value: null, measuredCount: 0, totalCount: 0, measurementState: "no_usage", measurementReason: "no_usage" },
          activity: "dormant",
          activityLabel: "休眠ユーザー",
        },
      ],
    }, "global");
  });
  expect(modelState.scopeUserCount).toBeNull();
  expect(modelState.contentDiagnostics.exportAvailable).toBeFalsy();
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
  expect(requests.some((row) => row.path === "/api/export/jobs")).toBeFalsy();
});

test("user map chooser and user detail keep roles outside the summary contract", async ({ page }) => {
  await installApiMocks(page, {
    usersOverride: {
      users: [
        analyticsUser({ rosterId: "roster_1", name: "User Map Member", role: "本社メンバー" }),
        analyticsUser({ rosterId: "user_map_unknown", name: "User Map Unknown", role: "未知の役割" }),
      ],
    },
    detailOverride: {
      profile: {
        ...detail.profile,
        name: "User Map Member",
        role: "本社メンバー",
      },
    },
  });

  await page.goto("/dashboard?page=user");
  await expect(page.locator(".userChoice")).toHaveCount(2);
  await expect(page.locator("#userChoices")).toContainText("User Map Member");
  await expect(page.locator("#userChoices")).toContainText("User Map Unknown");

  await page.locator('[data-roster="roster_1"]').click();
  await expect(page.locator('[data-module="profile"]')).toContainText("User Map Member");
  await expect(page.locator('[data-module="profile"]')).toContainText("本社メンバー");
});

test("management keeps a non-summary role visible as user-analysis only", async ({ page }) => {
  const member = {
    ...managedUsers.users[0],
    rosterId: "management_member",
    name: "Management Member",
    email: "management-member@example.com",
    role: "本社メンバー",
    globalScopeEnabled: false,
    userMapScopeEnabled: true,
  };
  await installApiMocks(page, { managedUsersOverride: { users: [member] } });

  await page.goto("/dashboard?page=management");

  await expect(page.locator("#managementUserResults")).toContainText("Management Member");
  await expect(page.locator("#managementUserResults .scopeBadge")).toHaveText("ユーザー分析のみ");
});

test("management rejects a server summary-role set that differs from the shared exact contract", async ({ page }) => {
  await installApiMocks(page, {
    managementMetadataOverride: {
      summaryRoles: ["本社MR", "コントラクトMR", "本社メンバー"],
    },
  });

  await page.goto("/dashboard?page=management");

  await expect(page.locator("#managementPanel")).toContainText("山田 太郎");
  await expect(page.locator("#managementPanel")).toContainText("対象判定未確認");
  await expect(page.locator("#managementPanel")).toContainText("全体サマリー対象の役割契約が一致しません");
});
