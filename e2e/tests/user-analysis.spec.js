const { test, expect } = require("@playwright/test");
const { detail, installApiMocks, makeAnalyticsUsers, users } = require("./fixtures");

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

for (const diagnosticCase of [
  {
    name: "missing",
    value: null,
    message: "ラベル情報と名簿診断情報の状態を確認できません。利用状況は表示しています。",
  },
  {
    name: "unavailable",
    value: { state: "degraded", labelCatalogStatus: "unavailable", issues: ["label_catalog_unavailable"] },
    message: "ラベル情報を取得できません。利用状況は表示しています。",
  },
]) {
  test(`${diagnosticCase.name} label diagnostics preserve personal analytics and disable export`, async ({ page }) => {
    await installApiMocks(page, { detailOverride: { contentDiagnostics: diagnosticCase.value } });
    await page.goto("/dashboard?page=user&roster=roster_1");

    await expect(page.locator('[data-module="profile"]')).toContainText("山田 太郎");
    await expect(page.locator('[data-module="profile"]')).toContainText(diagnosticCase.message);
    await expect(page.locator('[data-module="summary"]')).toContainText("回答成功率");
    await expect(page.locator('[data-module="needs"]')).toContainText("情報確認");
    await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
  });
}

test("historical mode and device gaps are explained without fake unknown charts", async ({ page }) => {
  await installApiMocks(page, {
    detailOverride: {
      modes: [],
      modeMeasurement: { measuredCount: 0, totalCount: 20, measurementState: "not_measured", measurementReason: "historical_unavailable" },
      devices: [],
      deviceMeasurement: { measuredCount: 0, totalCount: 20, measurementState: "not_measured", measurementReason: "historical_unavailable" },
    },
  });
  await page.goto("/dashboard?page=user&roster=roster_1");
  await expect(page.locator("[data-freshness-banner]")).toContainText("公開済みの集計データを表示しています");
  await expect(page.locator("[data-freshness-banner]")).not.toContainText("反映済み");
  await expect(page.locator("[data-freshness-banner]")).not.toContainText("2026/08/23");
  await expect(page.locator("[data-freshness-banner]")).not.toContainText("3時間ごと");
  await expect(page.locator('[data-module="trend"]')).toContainText("途中集計");
  await expect(page.locator('[data-module="trend"]')).not.toContainText("反映済み時刻");
  const needs = page.locator('[data-module="needs"]');
  await expect(needs).toContainText("モード");
  await expect(needs).toContainText("デバイス");
  await expect(needs.locator(".needsSecondary .moduleMessage")).toHaveCount(2);
  await expect(needs.locator(".needsSecondary")).toContainText("過去データにはこの項目が保存されていません");
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

test("missing legacy P95 and one broken need axis do not erase personal summary or other needs", async ({ page }) => {
  const legacySummary = { ...detail.summary };
  delete legacySummary.p95Latency;
  await installApiMocks(page, {
    detailOverride: {
      scopePolicyVersion: null,
      rosterFingerprint: null,
      publishedRunId: null,
      summary: legacySummary,
      modeMeasurement: { measuredCount: 21, totalCount: 20, measurementState: "measured", measurementReason: "complete" },
    },
  });
  await page.goto("/dashboard?page=user&roster=roster_1");

  await expect(page.locator('[data-module="summary"]')).toContainText("回答成功率");
  await expect(page.locator('[data-module="summary"]')).toContainText("P95応答時間");
  await expect(page.locator('[data-module="summary"]')).toContainText("計測情報なし");
  await expect(page.locator('[data-module="needs"]')).toContainText("テルフュージョン");
  await expect(page.locator('[data-module="needs"]')).toContainText("情報確認");
  await expect(page.locator('[data-module="needs"]')).toContainText("デバイス");
  await expect(page.locator('[data-module="needs"]')).toContainText("モード分析の計測範囲");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

test("missing profile metadata and one malformed trend row keep the valid personal body", async ({ page }) => {
  await installApiMocks(page, {
    detailOverride: {
      profile: { ...detail.profile, role: "" },
      trend: [
        detail.trend[0],
        { ...detail.trend[1], questions: -1 },
      ],
    },
  });
  await page.goto("/dashboard?page=user&roster=roster_1");

  await expect(page.locator('[data-module="profile"]')).toContainText("山田 太郎");
  await expect(page.locator('[data-module="profile"]')).toContainText("未取得");
  await expect(page.locator('[data-module="trend"] #personalTrend')).toHaveCount(1);
  await expect(page.locator('[data-module="trend"]')).toContainText("2行目");
  await expect(page.locator('[data-module="needs"]')).toContainText("情報確認");
});

test("one chart runtime failure on first render keeps every sibling body", async ({ page }) => {
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function getContextWithOneFailure(...args) {
      if (this.id === "personalProducts") {
        throw new Error("製品グラフだけを表示できません");
      }
      return original.apply(this, args);
    };
  });
  await installApiMocks(page);
  await page.goto("/dashboard?page=user&roster=roster_1");

  await expect(page.locator('[data-module="profile"]')).toContainText("山田 太郎");
  await expect(page.locator('[data-module="summary"]')).toContainText("回答成功率");
  await expect(page.locator('[data-module="trend"] #personalTrend')).toHaveCount(1);
  await expect(page.locator('[data-module="needs"]')).toContainText("製品グラフだけを表示できません");
  await expect(page.locator('[data-module="needs"]')).toContainText("質問テーマ");
  await expect(page.locator('[data-module="needs"]')).toContainText("情報確認");
  await expect(page.locator('[data-module="needs"] #personalCategories')).toHaveCount(1);
  await expect(page.getByRole("button", { name: "CSV" })).toBeEnabled();
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

test("a pending chooser refresh commits the latest local search and page", async ({ page }) => {
  const initialRows = makeAnalyticsUsers(80).map((row, index) => ({
    ...row,
    name: `対象 ${String(index + 1).padStart(2, "0")}`,
  }));
  const refreshedRows = makeAnalyticsUsers(80).map((row, index) => ({
    ...row,
    name: `対象更新 ${String(index + 1).padStart(2, "0")}`,
  }));
  let holdRefresh = false;
  let markRefreshStarted;
  let releaseRefresh;
  const refreshStarted = new Promise((resolve) => { markRefreshStarted = resolve; });
  const refreshReleased = new Promise((resolve) => { releaseRefresh = resolve; });
  await installApiMocks(page, { usersOverride: { users: initialRows } });
  await page.route(/\/api\/analytics\/users(?:\?.*)?$/, async (route) => {
    if (!holdRefresh) return route.fallback();
    markRefreshStarted();
    await refreshReleased;
    return route.fulfill({ json: { ...users, users: refreshedRows } });
  });
  await page.goto("/dashboard?page=user");
  await expect(page.locator(".chooserPanel")).toContainText("1–15 / 80名");

  holdRefresh = true;
  await page.getByRole("button", { name: "再読込" }).click();
  await refreshStarted;
  await page.locator("#userSearch").fill("対象");
  await page.getByRole("button", { name: "次のページ" }).click();
  await expect(page.locator(".chooserPanel")).toContainText("16–30 / 80名");

  releaseRefresh();
  await expect(page.locator('.userChoice[data-roster="roster_16"]')).toContainText("対象更新 16");
  await expect(page.locator("#userSearch")).toHaveValue("対象");
  await expect(page.locator(".chooserPanel")).toContainText("16–30 / 80名");
  expect(await page.evaluate(() => ({
    query: new URL(window.location.href).searchParams.get("user_q"),
    page: new URL(window.location.href).searchParams.get("user_page"),
  }))).toEqual({ query: "対象", page: "2" });
});

test("a pending chooser refresh cannot overwrite a newer roster selection", async ({ page }) => {
  let holdNextChooserRefresh = false;
  let heldChooserRefresh = false;
  let markRefreshStarted;
  let releaseRefresh;
  let markRefreshDelivered;
  const refreshStarted = new Promise((resolve) => { markRefreshStarted = resolve; });
  const refreshReleased = new Promise((resolve) => { releaseRefresh = resolve; });
  const refreshDelivered = new Promise((resolve) => { markRefreshDelivered = resolve; });
  await installApiMocks(page);
  await page.route(/\/api\/analytics\/users(?:\?.*)?$/, async (route) => {
    if (!holdNextChooserRefresh || heldChooserRefresh) return route.fallback();
    heldChooserRefresh = true;
    markRefreshStarted();
    await refreshReleased;
    await route.fulfill({ json: users });
    markRefreshDelivered();
  });
  await page.goto("/dashboard?page=user");
  await expect(page.locator('.userChoice[data-roster="roster_1"]')).toContainText("山田 太郎");

  holdNextChooserRefresh = true;
  await page.getByRole("button", { name: "再読込" }).click();
  await refreshStarted;
  await page.locator('.userChoice[data-roster="roster_1"]').click();
  await expect(page.locator('[data-module="profile"]')).toContainText("山田 太郎");
  await expect(page).toHaveURL(/page=user.*roster=roster_1/);
  await expect(page.getByRole("button", { name: "CSV" })).toBeEnabled();

  releaseRefresh();
  await refreshDelivered;
  await page.waitForTimeout(100);
  await expect(page.locator('[data-module="profile"]')).toContainText("山田 太郎");
  await expect(page.locator(".chooserPanel")).toHaveCount(0);
  await expect(page).toHaveURL(/page=user.*roster=roster_1/);
  await expect(page.getByRole("button", { name: "CSV" })).toBeEnabled();
});

test("a shrinking chooser snapshot clamps the committed page and URL together", async ({ page }) => {
  const initialRows = makeAnalyticsUsers(80);
  const refreshedRows = makeAnalyticsUsers(10).map((row, index) => ({
    ...row,
    name: `縮小後 ${String(index + 1).padStart(2, "0")}`,
  }));
  let holdRefresh = false;
  let markRefreshStarted;
  let releaseRefresh;
  const refreshStarted = new Promise((resolve) => { markRefreshStarted = resolve; });
  const refreshReleased = new Promise((resolve) => { releaseRefresh = resolve; });
  await installApiMocks(page, { usersOverride: { users: initialRows } });
  await page.route(/\/api\/analytics\/users(?:\?.*)?$/, async (route) => {
    if (!holdRefresh) return route.fallback();
    markRefreshStarted();
    await refreshReleased;
    return route.fulfill({ json: { ...users, scopeUserCount: 10, users: refreshedRows } });
  });
  await page.goto("/dashboard?page=user&user_page=6");
  await expect(page.locator(".chooserPanel")).toContainText("76–80 / 80名");

  holdRefresh = true;
  await page.getByRole("button", { name: "再読込" }).click();
  await refreshStarted;
  releaseRefresh();

  await expect(page.locator('.userChoice[data-roster="roster_1"]')).toContainText("縮小後 01");
  await expect(page.locator(".userChoice")).toHaveCount(10);
  await expect(page.locator(".chooserPanel")).toContainText("1–10 / 10名");
  expect(await page.evaluate(() => new URL(window.location.href).searchParams.get("user_page"))).toBeNull();
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

test("chooser and detail share one analysis anchor while trace stays all-history", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, { requests });
  await page.goto("/dashboard?page=user");
  await expect(page.locator('.userChoice[data-roster="roster_1"]')).toBeVisible();
  await page.locator('.userChoice[data-roster="roster_1"]').click();
  await expect(page.locator('[data-module="profile"]')).toContainText("山田 太郎");
  await expect(page.locator("#conversationList")).toContainText("製品情報の確認");

  const chooserRequest = requests.find((row) => row.path === "/api/analytics/users");
  const detailRequest = requests.find((row) => row.path === "/api/analytics/users/roster_1");
  expect(chooserRequest).toBeTruthy();
  expect(detailRequest).toBeTruthy();
  const chooserQuery = new URLSearchParams(chooserRequest.search);
  const detailQuery = new URLSearchParams(detailRequest.search);
  expect(chooserQuery.get("preset")).toBe("last_7d");
  expect(detailQuery.get("preset")).toBe("last_7d");
  expect(chooserQuery.get("as_of")).toBeTruthy();
  expect(detailQuery.get("as_of")).toBe(chooserQuery.get("as_of"));

  const traceRequests = requests.filter((row) => row.path.startsWith("/api/trace/"));
  expect(traceRequests.length).toBeGreaterThan(0);
  expect(traceRequests.every((row) => !new URLSearchParams(row.search).has("as_of"))).toBeTruthy();
});

test("an expired chooser anchor refreshes list and detail with one new analysis anchor", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, { requests });
  await page.goto("/dashboard?page=user");
  await expect(page.locator('.userChoice[data-roster="roster_1"]')).toBeVisible();

  const initialList = requests.find((row) => row.path === "/api/analytics/users");
  const initialAnchor = new URLSearchParams(initialList.search).get("as_of");
  expect(initialAnchor).toBeTruthy();
  await page.clock.setFixedTime(new Date(Date.parse(initialAnchor) + 5 * 60 * 1000));

  await page.locator('.userChoice[data-roster="roster_1"]').click();
  await expect(page.locator('[data-module="profile"]')).toContainText("山田 太郎");

  const listRequests = requests.filter((row) => row.path === "/api/analytics/users");
  const detailRequests = requests.filter((row) => row.path === "/api/analytics/users/roster_1");
  expect(listRequests).toHaveLength(2);
  expect(detailRequests).toHaveLength(1);
  const refreshedListQuery = new URLSearchParams(listRequests[1].search);
  const detailQuery = new URLSearchParams(detailRequests[0].search);
  expect(refreshedListQuery.get("preset")).toBe("last_7d");
  expect(detailQuery.get("preset")).toBe("last_7d");
  expect(refreshedListQuery.get("as_of")).toBeTruthy();
  expect(refreshedListQuery.get("as_of")).not.toBe(initialAnchor);
  expect(detailQuery.get("as_of")).toBe(refreshedListQuery.get("as_of"));
  await expect(page.getByRole("button", { name: "CSV" })).toBeEnabled();
});

test("a failed expired chooser transaction keeps the chooser route and disables CSV", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, { requests, failDetail: true });
  await page.goto("/dashboard?page=user");
  await expect(page.locator('.userChoice[data-roster="roster_1"]')).toBeVisible();
  await page.locator(".chooserPanel").evaluate((element) => { element.dataset.expiredAnchorDom = "kept"; });

  const initialList = requests.find((row) => row.path === "/api/analytics/users");
  const initialAnchor = new URLSearchParams(initialList.search).get("as_of");
  await page.clock.setFixedTime(new Date(Date.parse(initialAnchor) + 5 * 60 * 1000));
  await page.locator('.userChoice[data-roster="roster_1"]').click();

  await expect(page.locator("[data-freshness-banner] [data-user-anchor-error]")).toHaveText(
    "分析期間を更新できませんでした。表示中の内容を保持しています。",
  );
  await expect(page).toHaveURL(/page=user(?!.*roster)/);
  await expect(page.locator(".chooserPanel")).toHaveAttribute("data-expired-anchor-dom", "kept");
  await expect(page.locator('.userChoice[data-roster="roster_1"]')).toContainText("山田 太郎");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();

  const listRequests = requests.filter((row) => row.path === "/api/analytics/users");
  const detailRequests = requests.filter((row) => row.path === "/api/analytics/users/roster_1");
  expect(listRequests).toHaveLength(2);
  expect(detailRequests).toHaveLength(1);
  const refreshedAnchor = new URLSearchParams(listRequests[1].search).get("as_of");
  expect(refreshedAnchor).toBeTruthy();
  expect(refreshedAnchor).not.toBe(initialAnchor);
  expect(new URLSearchParams(detailRequests[0].search).get("as_of")).toBe(refreshedAnchor);
});

test("an ordinary failed refresh preserves the committed personal body, URL and charts", async ({ page }) => {
  let failRefresh = false;
  let markRefreshStarted;
  let releaseRefresh;
  const refreshStarted = new Promise((resolve) => { markRefreshStarted = resolve; });
  const refreshReleased = new Promise((resolve) => { releaseRefresh = resolve; });
  await installApiMocks(page);
  await page.route(/\/api\/analytics\/users\/roster_1(?:\?.*)?$/, async (route) => {
    if (!failRefresh) return route.fallback();
    markRefreshStarted();
    await refreshReleased;
    return route.fulfill({
      status: 503,
      json: { detail: { code: "source_unavailable", message: "personal refresh unavailable" } },
    });
  });
  await page.goto("/dashboard?page=user&roster=roster_1");
  await expect(page.locator('[data-module="profile"]')).toContainText("山田 太郎");
  await expect(page.locator("#personalTrend")).toHaveCount(1);
  const committedUrl = page.url();
  await page.locator('[data-module="profile"]').evaluate((element) => { element.dataset.committedPersonal = "kept"; });
  await page.locator("#personalTrend").evaluate((element) => { element.dataset.committedChart = "kept"; });

  failRefresh = true;
  await page.getByRole("button", { name: "再読込" }).click();
  await refreshStarted;
  await expect(page).toHaveURL(committedUrl);
  await expect(page.locator('[data-module="profile"]')).toHaveAttribute("data-committed-personal", "kept");
  await expect(page.locator("#personalTrend")).toHaveAttribute("data-committed-chart", "kept");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();

  releaseRefresh();
  await expect(page.locator("[data-freshness-banner] [data-user-anchor-error]")).toHaveText(
    "分析期間を更新できませんでした。表示中の内容を保持しています。",
  );
  await expect(page).toHaveURL(committedUrl);
  await expect(page.locator('[data-module="profile"]')).toHaveAttribute("data-committed-personal", "kept");
  await expect(page.locator("#personalTrend")).toHaveAttribute("data-committed-chart", "kept");
});

test("a DOM commit fault preserves the old personal chart instance and body", async ({ page }) => {
  await installApiMocks(page);
  await page.goto("/dashboard?page=user&roster=roster_1");
  await expect(page.locator("#personalTrend")).toHaveCount(1);
  await page.locator("#personalTrend").evaluate((element) => { element.dataset.commitFaultChart = "kept"; });
  await page.locator('[data-module="profile"]').evaluate((element) => { element.dataset.commitFaultBody = "kept"; });
  await page.evaluate(() => {
    const originalDestroy = window.Chart.prototype.destroy;
    window.__committedPersonalDestroyCount = 0;
    window.Chart.prototype.destroy = function trackedDestroy(...args) {
      if (this.canvas?.dataset?.commitFaultChart === "kept") window.__committedPersonalDestroyCount += 1;
      return originalDestroy.apply(this, args);
    };
    const pageRoot = document.querySelector("#pageRoot");
    pageRoot.replaceChildren = () => { throw new Error("personal DOM commit failed"); };
  });

  await page.getByRole("button", { name: "再読込" }).click();

  await expect(page.locator("[data-freshness-banner] [data-user-anchor-error]")).toHaveText(
    "分析期間を更新できませんでした。表示中の内容を保持しています。",
  );
  await expect(page.locator('[data-module="profile"]')).toHaveAttribute("data-commit-fault-body", "kept");
  await expect(page.locator("#personalTrend")).toHaveAttribute("data-commit-fault-chart", "kept");
  expect(await page.evaluate(() => window.__committedPersonalDestroyCount)).toBe(0);
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

test("a captured model rejection during refresh cannot replace the committed personal analysis", async ({ page }) => {
  let rejectProfile = false;
  await installApiMocks(page);
  await page.route(/\/api\/analytics\/users\/roster_1(?:\?.*)?$/, async (route) => {
    if (!rejectProfile) return route.fallback();
    return route.fulfill({ json: { ...detail, profile: null } });
  });
  await page.goto("/dashboard?page=user&roster=roster_1");
  await expect(page.locator('[data-module="profile"]')).toContainText("山田 太郎");
  await expect(page.locator("#personalTrend")).toHaveCount(1);
  const committedUrl = page.url();
  await page.locator('[data-module="profile"]').evaluate((element) => { element.dataset.capturedModelDom = "kept"; });
  await page.locator("#personalTrend").evaluate((element) => { element.dataset.capturedModelChart = "kept"; });

  rejectProfile = true;
  await page.getByRole("button", { name: "再読込" }).click();

  await expect(page.locator("[data-freshness-banner] [data-user-anchor-error]")).toHaveText(
    "分析期間を更新できませんでした。表示中の内容を保持しています。",
  );
  await expect(page).toHaveURL(committedUrl);
  await expect(page.locator('[data-module="profile"]')).toHaveAttribute("data-captured-model-dom", "kept");
  await expect(page.locator('[data-module="profile"]')).toContainText("山田 太郎");
  await expect(page.locator("#personalTrend")).toHaveAttribute("data-captured-model-chart", "kept");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

test("a same-user analytics refresh reuses the committed conversation journey even if a new trace request would fail", async ({ page }) => {
  const requests = [];
  let refreshAnalytics = false;
  let rejectNewConversationRequest = false;
  await installApiMocks(page, { requests });
  await page.route(/\/api\/analytics\/users\/roster_1(?:\?.*)?$/, async (route) => {
    if (!refreshAnalytics) return route.fallback();
    return route.fulfill({ json: { ...detail, summary: { ...detail.summary, questions: 99 } } });
  });
  await page.route(/\/api\/trace\/conversations(?:\?.*)?$/, async (route) => {
    if (!rejectNewConversationRequest) return route.fallback();
    return route.fulfill({
      status: 503,
      json: { detail: { code: "source_unavailable", message: "trace refresh unavailable" } },
    });
  });
  await page.goto("/dashboard?page=user&roster=roster_1");
  await expect(page.locator("#conversationList")).toContainText("製品情報の確認");
  await expect(page.locator("#messageList")).toContainText("製品の仕様を教えてください");
  await page.locator('[data-module="conversations"]').evaluate((element) => { element.dataset.committedJourney = "kept"; });
  const initialConversationRequests = requests.filter((row) => row.path === "/api/trace/conversations").length;

  refreshAnalytics = true;
  rejectNewConversationRequest = true;
  await page.getByRole("button", { name: "再読込" }).click();

  await expect(page.locator('[data-module="summary"]')).toContainText("99");
  await expect(page.locator('[data-module="conversations"]')).toHaveAttribute("data-committed-journey", "kept");
  await expect(page.locator("#conversationList")).toContainText("製品情報の確認");
  await expect(page.locator("#messageList")).toContainText("製品の仕様を教えてください");
  expect(requests.filter((row) => row.path === "/api/trace/conversations")).toHaveLength(initialConversationRequests);
});

test("a delayed old-user conversation response cannot overwrite a newly selected user", async ({ page }) => {
  let releaseOldConversation;
  let markOldConversationStarted;
  const oldConversationStarted = new Promise((resolve) => { markOldConversationStarted = resolve; });
  const oldConversationReleased = new Promise((resolve) => { releaseOldConversation = resolve; });
  await installApiMocks(page);
  await page.route(/\/api\/analytics\/users\/roster_2(?:\?.*)?$/, async (route) => route.fulfill({
    json: {
      ...detail,
      profile: {
        ...detail.profile,
        rosterId: "roster_2",
        name: "佐藤 花子",
        email: "user2@example.com",
        role: "コントラクトMR",
        department: "DM本社",
        area: "本社",
        workplace: "虎ノ門",
      },
    },
  }));
  await page.route(/\/api\/trace\/conversations(?:\?.*)?$/, async (route) => {
    const rosterId = new URL(route.request().url()).searchParams.get("roster_id");
    if (rosterId === "roster_1") {
      markOldConversationStarted();
      await oldConversationReleased;
      return route.fulfill({ json: {
        status: "ready",
        conversations: [{ conversationId: "old_conv", title: "旧ユーザーの会話", messageCount: 1, updatedAt: "2026-08-23T01:00:00Z", updatedAtJst: "2026-08-23 10:00:00" }],
      } });
    }
    return route.fulfill({ json: {
      status: "ready",
      conversations: [{ conversationId: "new_conv", title: "新ユーザーの会話", messageCount: 1, updatedAt: "2026-08-24T01:00:00Z", updatedAtJst: "2026-08-24 10:00:00" }],
    } });
  });
  await page.goto("/dashboard?page=user&roster=roster_1");
  await expect(page.locator('[data-module="profile"]')).toContainText("山田 太郎");
  await oldConversationStarted;

  await page.getByRole("button", { name: "別のユーザーを選択" }).click();
  await expect(page.locator(".chooserPanel")).toBeVisible();
  await page.locator('.userChoice[data-roster="roster_2"]').click();
  await expect(page.locator('[data-module="profile"]')).toContainText("佐藤 花子");
  await expect(page.locator("#conversationList")).toContainText("新ユーザーの会話");

  releaseOldConversation();
  await page.waitForTimeout(300);
  await expect(page.locator('[data-module="profile"]')).toContainText("佐藤 花子");
  await expect(page.locator("#conversationList")).toContainText("新ユーザーの会話");
  await expect(page.locator("#conversationList")).not.toContainText("旧ユーザーの会話");
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
