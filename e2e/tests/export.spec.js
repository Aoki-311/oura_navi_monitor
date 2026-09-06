const { test, expect } = require("@playwright/test");
const { readFile } = require("node:fs/promises");
const { installApiMocks, overviewUsers } = require("./fixtures");
const { applyPreset } = require("./date-range-helpers");

const exportCreates = (requests) => requests.filter((row) => row.method === "POST" && row.path === "/api/export/jobs");
const exportDownloads = (requests, jobId = "") => requests.filter((row) => (
  row.method === "GET"
  && /^\/api\/export\/jobs\/[^/]+\/download$/.test(row.path)
  && (!jobId || row.path === `/api/export/jobs/${jobId}/download`)
));
const exportDeletes = (requests, jobId = "") => requests.filter((row) => (
  row.method === "DELETE"
  && /^\/api\/export\/jobs\/[^/]+$/.test(row.path)
  && (!jobId || row.path === `/api/export/jobs/${jobId}`)
));

async function downloadText(download) {
  const path = await download.path();
  return readFile(path, "utf8");
}

test("summary CSV is anchored to the visible run and downloaded before success is shown", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, { requests });
  await page.goto("/dashboard?overview_q=%E5%B1%B1%E7%94%B0&overview_activity=high&overview_sort=name_asc");
  const button = page.getByRole("button", { name: "CSV" });
  await expect(button).toBeEnabled();
  const downloadPromise = page.waitForEvent("download");
  await button.click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("monitor-job_1.csv");
  expect(await downloadText(download)).toContain('"山田","high","name_asc"');
  await expect(page.locator("#toast")).toContainText("CSVをダウンロードしました（1行）");

  const create = requests.find((row) => row.method === "POST" && row.path === "/api/export/jobs");
  expect(create.body).toMatchObject({
    kind: "overview_users",
    q: "山田",
    activity: "high",
    sort: "name_asc",
    expectedPublishedRunId: "run-20260823-01",
    expectedRosterFingerprint: "roster-fingerprint-1",
    expectedContentFingerprint: "content-fingerprint-1",
    expectedScopePolicyVersion: "summary_role_v1",
    expectedWindowStart: "2026-08-16T15:00:00Z",
    expectedWindowEnd: "2026-08-23T01:00:00Z",
    expectedWindowTimezone: "Asia/Tokyo",
  });
  expect(create.body.idempotencyKey.length).toBeGreaterThanOrEqual(8);
  expect(exportDownloads(requests, "job_1")).toHaveLength(1);
  await expect.poll(() => exportDeletes(requests, "job_1").length).toBe(1);
});

test("summary CSV waits until the latest filter transaction is visible", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, {
    requests,
    overviewUsersDelayByQuery: { "山田": 500 },
  });
  await page.goto("/dashboard");
  const button = page.getByRole("button", { name: "CSV" });
  await expect(button).toBeEnabled();

  await page.locator("#overviewUserSearch").fill("山田");
  await expect(button).toBeDisabled();
  await expect(button).toBeEnabled();

  const downloadPromise = page.waitForEvent("download");
  await button.click();
  const download = await downloadPromise;
  expect(await downloadText(download)).toContain('"山田","","last_desc"');
  const create = exportCreates(requests).at(-1);
  expect(create.body.q).toBe("山田");
});

for (const scenario of [
  {
    name: "search",
    mutate: (page) => page.locator("#overviewUserSearch").fill("山田"),
    assertRequest: (body) => expect(body.q).toBe("山田"),
  },
  {
    name: "activity",
    mutate: (page) => page.locator("#overviewActivity").selectOption("high"),
    assertRequest: (body) => expect(body.activity).toBe("high"),
  },
  {
    name: "sort",
    mutate: (page) => page.locator("#overviewSort").selectOption("name_asc"),
    assertRequest: (body) => expect(body.sort).toBe("name_asc"),
  },
]) {
  test(`summary CSV cancels an in-flight export when ${scenario.name} changes`, async ({ page }) => {
    const requests = [];
    await installApiMocks(page, { requests, exportCreateDelay: 700 });
    await page.goto("/dashboard");
    const button = page.getByRole("button", { name: "CSV" });
    await expect(button).toBeEnabled();

    let downloadCount = 0;
    page.on("download", () => { downloadCount += 1; });
    const firstCreate = page.waitForRequest((request) => (
      request.method() === "POST" && new URL(request.url()).pathname === "/api/export/jobs"
    ));
    await button.click();
    await firstCreate;
    await scenario.mutate(page);

    await page.waitForTimeout(900);
    expect(downloadCount).toBe(0);
    await expect(page.locator("#toast")).not.toContainText("CSVをダウンロードしました");
    await expect(button).toBeEnabled();
    const firstCreateBody = exportCreates(requests)[0].body;
    expect(exportDownloads(requests, "job_1")).toHaveLength(0);

    const downloadPromise = page.waitForEvent("download");
    await button.click();
    const download = await downloadPromise;
    expect(downloadCount).toBe(1);
    const latestCreate = exportCreates(requests).at(-1);
    scenario.assertRequest(latestCreate.body);
    expect(latestCreate.body.idempotencyKey).not.toBe(firstCreateBody.idempotencyKey);
    expect(await downloadText(download)).toContain(latestCreate.body.idempotencyKey);
  });
}

test("summary CSV cancels a known job while its download response is in flight", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, { requests, exportDownloadDelay: 700 });
  await page.goto("/dashboard");
  const button = page.getByRole("button", { name: "CSV" });
  await expect(button).toBeEnabled();

  let downloadCount = 0;
  page.on("download", () => { downloadCount += 1; });
  const firstDownloadRequest = page.waitForRequest((request) => (
    request.method() === "GET" && new URL(request.url()).pathname === "/api/export/jobs/job_1/download"
  ));
  await button.click();
  await firstDownloadRequest;
  await page.locator("#overviewUserSearch").fill("山田");

  await expect.poll(() => exportDeletes(requests, "job_1").length).toBe(1);
  await page.waitForTimeout(800);
  expect(downloadCount).toBe(0);
  expect(exportDownloads(requests, "job_1")).toHaveLength(1);
  await expect(page.locator("#toast")).not.toContainText("CSVをダウンロードしました");
  await expect(button).toBeEnabled();

  const downloadPromise = page.waitForEvent("download");
  await button.click();
  const download = await downloadPromise;
  expect(downloadCount).toBe(1);
  expect(await downloadText(download)).toContain('"山田","","last_desc"');
  const creates = exportCreates(requests);
  expect(creates).toHaveLength(2);
  expect(creates[1].body.idempotencyKey).not.toBe(creates[0].body.idempotencyKey);
});

test("download is the UI commit boundary and cleanup delay does not keep CSV busy", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, { requests, exportDeleteDelay: 1200 });
  await page.goto("/dashboard");
  const button = page.getByRole("button", { name: "CSV" });
  await expect(button).toBeEnabled();

  const deleteResponsePromise = page.waitForResponse((response) => (
    response.request().method() === "DELETE"
    && new URL(response.url()).pathname === "/api/export/jobs/job_1"
  ));
  const downloadPromise = page.waitForEvent("download");
  await button.click();
  await downloadPromise;

  await expect(button).toBeEnabled({ timeout: 500 });
  await expect(button).not.toHaveAttribute("aria-busy", "true", { timeout: 500 });
  await expect(page.locator("#toast")).toContainText("CSVをダウンロードしました（1行）", { timeout: 500 });
  await page.locator("#overviewUserSearch").fill("山田");
  await expect(button).toBeDisabled();
  await expect(button).toBeEnabled();
  await deleteResponsePromise;
  expect(exportDeletes(requests, "job_1")).toHaveLength(1);
});

test("a slow stale search cannot replace the latest export context", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, {
    requests,
    overviewUsersDelayByQuery: { A: 800, B: 50 },
    overviewUsersByQuery: {
      A: { users: [{ ...overviewUsers.users[0], name: "検索 A" }] },
      B: { users: [{ ...overviewUsers.users[0], name: "検索 B" }] },
    },
  });
  await page.goto("/dashboard");
  const button = page.getByRole("button", { name: "CSV" });
  const search = page.locator("#overviewUserSearch");
  await expect(button).toBeEnabled();

  const requestA = page.waitForRequest((request) => (
    new URL(request.url()).pathname === "/api/analytics/overview/users"
    && new URL(request.url()).searchParams.get("q") === "A"
  ));
  await search.fill("A");
  await requestA;
  const requestB = page.waitForRequest((request) => (
    new URL(request.url()).pathname === "/api/analytics/overview/users"
    && new URL(request.url()).searchParams.get("q") === "B"
  ));
  await search.fill("B");
  await requestB;
  await expect(button).toBeEnabled();
  await expect(page.locator("#overviewUsers")).toContainText("検索 B");
  await page.waitForTimeout(850);
  await expect(button).toBeEnabled();
  await expect(page.locator("#overviewUsers")).toContainText("検索 B");
  await expect(page.locator("#overviewUsers")).not.toContainText("検索 A");

  const downloadPromise = page.waitForEvent("download");
  await button.click();
  const download = await downloadPromise;
  const create = exportCreates(requests).at(-1);
  expect(create.body.q).toBe("B");
  expect(create.body.expectedContentFingerprint).toBe("content-fingerprint-1");
  expect(await downloadText(download)).toContain('"B","","last_desc"');
});

for (const scenario of [
  {
    name: "area",
    prepare: (page) => expect(page.locator('#regionRanking [data-area="関西"]')).toBeVisible(),
    mutate: (page) => page.locator('#regionRanking [data-area="関西"]').click(),
    assertRequest: (body) => expect(body.areaKey).toBe("関西"),
  },
  {
    name: "preset",
    prepare: async () => {},
    mutate: async (page) => {
      await applyPreset(page.locator("#mainPeriod"), "last_14d");
    },
    assertRequest: (body) => expect(body.preset).toBe("last_14d"),
  },
]) {
  test(`summary CSV invalidates an in-flight export when ${scenario.name} changes`, async ({ page }) => {
    const requests = [];
    let releaseFirstCreate;
    const firstCreateGate = new Promise((resolve) => { releaseFirstCreate = resolve; });
    await installApiMocks(page, {
      requests,
      beforeExportCreateResponse: (job) => job.jobId === "job_1" ? firstCreateGate : undefined,
    });
    await page.goto("/dashboard");
    const button = page.getByRole("button", { name: "CSV" });
    await expect(button).toBeEnabled();
    await scenario.prepare(page);

    let downloadCount = 0;
    page.on("download", () => { downloadCount += 1; });
    const firstCreate = page.waitForRequest((request) => (
      request.method() === "POST" && new URL(request.url()).pathname === "/api/export/jobs"
    ));
    await button.click();
    const firstRequest = await firstCreate;
    const firstCancelled = page.waitForEvent("requestfailed", (request) => request === firstRequest);
    await scenario.mutate(page);
    await expect(button).toBeEnabled();
    releaseFirstCreate();
    expect((await firstCancelled).failure().errorText).toContain("ERR_ABORTED");
    expect(downloadCount).toBe(0);
    expect(exportDownloads(requests)).toHaveLength(0);

    const downloadPromise = page.waitForEvent("download");
    await button.click();
    const download = await downloadPromise;
    const creates = exportCreates(requests);
    expect(creates).toHaveLength(2);
    scenario.assertRequest(creates[1].body);
    expect(creates[1].body.idempotencyKey).not.toBe(creates[0].body.idempotencyKey);
    expect(await downloadText(download)).toContain(creates[1].body.idempotencyKey);
  });
}

test("same export context reuses its idempotency key but a changed context gets a new key", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, { requests, exportDownloadFailures: 1 });
  await page.goto("/dashboard");
  const button = page.getByRole("button", { name: "CSV" });
  await expect(button).toBeEnabled();

  const firstDeleteResponse = page.waitForResponse((response) => (
    response.request().method() === "DELETE"
    && new URL(response.url()).pathname === "/api/export/jobs/job_1"
  ));
  await button.click();
  await expect(page.locator("#toast")).toContainText("データを読み込めませんでした");
  await expect(button).toBeEnabled();
  await firstDeleteResponse;

  const retryDownloadPromise = page.waitForEvent("download");
  await button.click();
  const retryDownload = await retryDownloadPromise;
  expect(await downloadText(retryDownload)).toContain('"job_1"');
  const retryCreates = exportCreates(requests);
  expect(retryCreates).toHaveLength(2);
  expect(retryCreates[1].body.idempotencyKey).toBe(retryCreates[0].body.idempotencyKey);

  await page.locator("#overviewUserSearch").fill("山田");
  await expect(button).toBeEnabled();
  const changedDownloadPromise = page.waitForEvent("download");
  await button.click();
  const changedDownload = await changedDownloadPromise;
  const changedCreate = exportCreates(requests).at(-1);
  expect(changedCreate.body.idempotencyKey).not.toBe(retryCreates[0].body.idempotencyKey);
  expect(await downloadText(changedDownload)).toContain('"山田","","last_desc"');
});

test("user CSV stays disabled when personal analytics fails even if conversations remain", async ({ page }) => {
  await installApiMocks(page, { failDetail: true });
  await page.goto("/dashboard?page=user&roster=roster_1");
  await expect(page.locator("#conversationList")).toContainText("製品情報の確認");
  await expect(page.getByRole("button", { name: "CSV" })).toBeDisabled();
});

test("an invalid export response cannot start a download or show false success", async ({ page }) => {
  const requests = [];
  await installApiMocks(page, { requests, invalidExportResponse: true });
  await page.goto("/dashboard");
  await page.getByRole("button", { name: "CSV" }).click();
  await expect(page.locator("#toast")).toContainText("CSVのダウンロード先が不正です");
  expect(requests.some((row) => row.path.endsWith("/download"))).toBeFalsy();
  await expect(page.locator("#toast")).not.toContainText("ダウンロードしました");
});
