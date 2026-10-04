const { test, expect } = require("@playwright/test");
const { installApiMocks, overview, overviewUsers, regions, newsUsage, managedUsers } = require("./fixtures");

const cohortPaths = [
  "/api/analytics/overview", "/api/analytics/regions", "/api/analytics/overview/users",
  "/api/analytics/environment", "/api/analytics/trend", "/api/news-usage/overview",
];

async function installCohortMocks(page, requests, { delayedCohort = "", failCohort = "", mismatchedCohort = "" } = {}) {
  await installApiMocks(page, { requests });
  await page.route(/\/api\/(analytics|news-usage)\//, async (route) => {
    const url = new URL(route.request().url());
    if (!cohortPaths.includes(url.pathname)) return route.fallback();
    const cohort = url.searchParams.get("cohort") || "all";
    requests.push({ path: url.pathname, search: url.search, method: "GET" });
    if (cohort === delayedCohort) await new Promise((resolve) => setTimeout(resolve, 400));
    if (cohort === failCohort) return route.fulfill({ status: 503, json: { detail: "unavailable" } });
    const count = { all: 144, dm: 69, hcs: 75 }[cohort];
    const users = [
      { ...overviewUsers.users[0], rosterId: "dm_1", name: "DM利用者", department: "MR(DM)" },
      { ...overviewUsers.users[0], rosterId: "hcs_1", name: "HCS利用者", role: "社員MR", department: "MR(HCS)" },
    ].filter((row) => cohort === "all" || row.department === `MR(${cohort.toUpperCase()})`);
    const base = url.pathname.endsWith("/regions") ? regions
      : url.pathname.endsWith("/overview/users") ? { ...overviewUsers, users }
        : url.pathname.startsWith("/api/news-usage/") ? newsUsage
          : { ...overview, kpis: { ...overview.kpis, activeUsers: count } };
    return route.fulfill({ json: { ...base, scopeUserCount: count, cohort: cohort === mismatchedCohort ? "all" : cohort } });
  });
}

async function expectCohort(page, requests, cohort, count) {
  await expect(page.locator(`[data-cohort="${cohort}"]`)).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("[data-summary-total]")).toHaveText(`${count}名`);
  for (const path of cohortPaths) {
    await expect.poll(() => requests.some((row) => row.path === path && new URLSearchParams(row.search).get("cohort") === cohort)).toBeTruthy();
  }
}

test("MR tabs scope every overview module, preserve filters and roundtrip through export and history", async ({ page }) => {
  const requests = [];
  await installCohortMocks(page, requests, { delayedCohort: "hcs" });
  await page.goto("/dashboard?preset=last_30d&area=%E9%96%A2%E8%A5%BF");
  await expectCohort(page, requests, "all", 144);
  await expect(page.locator("#overviewUsers")).toContainText("DM利用者");
  await expect(page.locator("#overviewUsers")).toContainText("HCS利用者");
  await page.getByRole("tab", { name: "MR(HCS)", exact: true }).click();
  await expect(page).toHaveURL(/cohort=hcs/);
  await expect(page.locator("#overviewUsers")).toHaveCount(0);
  await expectCohort(page, requests, "hcs", 75);
  await expect(page.locator("#overviewUsers")).toContainText("HCS利用者");
  await expect(page.locator("#overviewUsers")).not.toContainText("DM利用者");
  expect(new URL(page.url()).searchParams.get("preset")).toBe("last_30d");
  expect(new URL(page.url()).searchParams.get("area")).toBe("関西");
  for (const path of cohortPaths.filter((path) => !path.endsWith("/regions"))) {
    const call = requests.find((row) => row.path === path && new URLSearchParams(row.search).get("cohort") === "hcs");
    expect(new URLSearchParams(call.search).get("area_key")).toBe("関西");
  }
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "CSV", exact: true }).click();
  await download;
  expect(requests.find((row) => row.path === "/api/export/jobs" && row.method === "POST").body).toMatchObject({ cohort: "hcs", areaKey: "関西", preset: "last_30d" });
  await page.getByRole("tab", { name: "MR(DM)", exact: true }).click();
  await expectCohort(page, requests, "dm", 69);
  await expect(page.locator("#overviewUsers")).toContainText("DM利用者");
  await page.goBack();
  await expectCohort(page, requests, "hcs", 75);
  await page.reload();
  await expectCohort(page, requests, "hcs", 75);
});

test("a failed cohort switch never leaves the previous cohort data under the new tab", async ({ page }) => {
  const requests = [];
  await installCohortMocks(page, requests, { failCohort: "hcs" });
  await page.goto("/dashboard");
  await expectCohort(page, requests, "all", 144);
  await page.getByRole("tab", { name: "MR(HCS)", exact: true }).click();
  await expect(page.locator('[data-cohort="hcs"]')).toHaveAttribute("aria-selected", "true");
  await expect(page.locator('[data-module="kpis"]')).toContainText("データを読み込めませんでした");
  await expect(page.locator("#overviewUsers")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "CSV", exact: true })).toBeDisabled();
});

test("a response from another cohort is rejected", async ({ page }) => {
  await installCohortMocks(page, [], { mismatchedCohort: "hcs" });
  await page.goto("/dashboard?cohort=hcs");
  await expect(page.locator('[data-module="kpis"]')).toContainText("選択したMR区分のデータを確認できません");
  await expect(page.locator("#overviewUsers")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "CSV", exact: true })).toBeDisabled();
});

test("HCS team and MR experience save and reopen, and leaving HCS clears the team", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, { requests });
  await page.goto("/dashboard?page=management");
  await page.getByRole("button", { name: "ユーザーを追加", exact: true }).click();
  await expect(page.locator('[name="team"]')).toBeDisabled();
  await page.locator('[name="name"]').fill("HCS 新規ユーザー");
  await page.locator('[name="email"]').fill("new-hcs@example.com");
  await page.locator('[name="area"]').selectOption("関西");
  await page.locator('[name="workplace"]').fill("大阪");
  await page.locator('[name="role"]').selectOption("社員MR");
  await page.locator('[name="department"]').selectOption("MR(HCS)");
  await expect(page.locator('[name="team"]')).toBeEnabled();
  await expect(page.locator('[name="team"]')).not.toHaveAttribute("required");
  await page.locator('[name="team"]').fill("西日本チーム");
  await page.locator('[name="mr_experience"]').fill("11~20年");
  await expect(page.locator("#scopeImpact")).toContainText("全体サマリーとユーザー分析");
  await page.locator("#userForm").getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.locator(".drawer")).toHaveCount(0);
  const created = requests.find((row) => row.path === "/api/admin/users" && row.method === "POST");
  expect(created.body).toMatchObject({ department: "MR(HCS)", team: "西日本チーム", mr_experience: "11~20年" });
  const row = page.locator("#managementUserResults tbody tr").filter({ hasText: "new-hcs@example.com" });
  await expect(row).toContainText("チーム: 西日本チーム");
  await row.getByRole("button", { name: "編集", exact: true }).click();
  await expect(page.locator('[name="team"]')).toHaveValue("西日本チーム");
  await page.locator('[name="department"]').selectOption("MR(DM)");
  await expect(page.locator('[name="team"]')).toBeDisabled();
  await expect(page.locator('[name="team"]')).toHaveValue("");
  await expect(page.locator("#userForm").getByRole("button", { name: "保存", exact: true })).toBeEnabled();
  await page.locator("#userForm").getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.locator(".drawer")).toHaveCount(0);
  expect(requests.find((row) => row.path.startsWith("/api/admin/users/") && row.method === "PATCH").body).toMatchObject({ department: "MR(DM)", team: "" });
  await row.getByRole("button", { name: "編集", exact: true }).click();
  await expect(page.locator('[name="team"]')).toHaveValue("");
});

test("an existing HCS user can save an empty optional team and legacy DM remains editable", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, { requests, managedUsersOverride: { users: [{ ...managedUsers.users[0], department: "MR(HCS)", team: "" }] } });
  await page.goto("/dashboard?page=management&roster=roster_1");
  await expect(page.locator('[name="team"]')).toBeEnabled();
  await page.locator('[name="name"]').fill("HCS チーム未定");
  await page.locator("#userForm").getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.locator(".drawer")).toHaveCount(0);
  expect(requests.find((row) => row.method === "PATCH").body.team).toBe("");
  await installApiMocks(page, { managedUsersOverride: { users: [{ ...managedUsers.users[0], department: "DM専任" }] } });
  await page.goto("/dashboard?page=management&roster=roster_1");
  await expect(page.locator('[name="department"]')).toHaveValue("MR(DM)");
  await expect(page.locator('[name="team"]')).toBeDisabled();
});


test("MR tabs support keyboard navigation without losing tab focus", async ({ page }) => {
  const requests = [];
  await installCohortMocks(page, requests);
  await page.goto("/dashboard");
  await expectCohort(page, requests, "all", 144);
  await page.locator('[data-cohort="all"]').focus();
  await page.keyboard.press("ArrowRight");
  await expectCohort(page, requests, "dm", 69);
  await expect(page.locator('[data-cohort="dm"]')).toBeFocused();
  await page.keyboard.press("ArrowRight");
  await expectCohort(page, requests, "hcs", 75);
  await expect(page.locator('[data-cohort="hcs"]')).toBeFocused();
});
