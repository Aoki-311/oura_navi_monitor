const { test, expect } = require("@playwright/test");

function dashboardPayload(preset = "today") {
  const days = preset === "last_30d" ? 30 : preset === "last_14d" ? 14 : 7;
  const usageTrend = Array.from({ length: Math.min(days, 14) }, (_, index) => ({
    date: `05-${String(index + 1).padStart(2, "0")}`,
    activeUserCount: 12 + index,
    messageCount: 80 + index * 7,
  }));
  return {
    meta: {
      generatedAt: "2026-05-16T01:00:00Z",
      fetchMs: 42,
      metricStatus: { answerSuccessRate: "official" },
    },
    kpis: {
      activeUserCount: 36,
      answerSuccessRate: 0.94,
      lowCoverageRate: 0.07,
      errorRate: 0.012,
      p95LatencyMs: 1280,
      totalUserCount: 12856,
    },
    usageTrend,
    activityDistribution: {
      totalUserCount: 12856,
      segments: [
        { label: "高アクティブ", count: 1842, rate: 0.1433, activityKey: "high" },
        { label: "中アクティブ", count: 2840, rate: 0.2208, activityKey: "middle" },
        { label: "低アクティブ", count: 3756, rate: 0.292 },
        { label: "休眠ユーザー", count: 4418, rate: 0.3439 },
      ],
    },
    environmentMode: {
      requestByHour: Array.from({ length: 24 }, (_, hour) => ({
        hour: `${String(hour).padStart(2, "0")}:00`,
        requestCount: 10 + hour,
      })),
      deviceDistribution: [
        { label: "PC", count: 720, rate: 0.72 },
        { label: "モバイル", count: 260, rate: 0.26 },
        { label: "不明", count: 20, rate: 0.02 },
      ],
      modeDistribution: [
        { label: "社内モード", count: 650, rate: 0.65 },
        { label: "Web検索モード", count: 350, rate: 0.35 },
      ],
    },
    answerQuality: {
      answerability: [
        { label: "fully_answerable", count: 82, rate: 0.82 },
        { label: "partially_answerable", count: 14, rate: 0.14 },
        { label: "not_answerable", count: 4, rate: 0.04 },
      ],
      usability: [
        { label: "ready", count: 88, rate: 0.88 },
        { label: "bounded", count: 9, rate: 0.09 },
        { label: "not_ready", count: 3, rate: 0.03 },
      ],
      deliveryReadiness: [
        { label: "ready", count: 80, rate: 0.8 },
        { label: "bounded", count: 16, rate: 0.16 },
        { label: "not_ready", count: 4, rate: 0.04 },
      ],
      evidenceSufficiency: [
        { label: "sufficient", count: 74, rate: 0.74 },
        { label: "partial", count: 19, rate: 0.19 },
        { label: "insufficient", count: 7, rate: 0.07 },
      ],
    },
    followup: {
      recognizedCount: 41,
      successCount: 34,
      successRate: 0.829,
      explicitCorrectionCount: 5,
      clarificationRequiredCount: 3,
    },
  };
}

function usersPayload() {
  return {
    users: [
      {
        userId: "1000001",
        userEmail: "1000001@tc.terumo.co.jp",
        lastActiveAtJst: "2026/05/16 09:00:00",
        activeDays7: 6,
        messageCount7d: 22,
        coverageRate: 0.93,
        badFeedbackRate: 0.02,
        activityLevel: "高アクティブ",
      },
      {
        userId: "unknown",
        userEmail: "unknown",
        lastActiveAtJst: "2026/05/16 09:00:00",
        activeDays7: 1,
        messageCount7d: 1,
        coverageRate: 0.5,
        badFeedbackRate: 0,
        activityLevel: "低アクティブ",
      },
      {
        userId: "lcs-agent@lcs-developer-483404.iam.gserviceaccount.com",
        userEmail: "lcs-agent@lcs-developer-483404.iam.gserviceaccount.com",
        lastActiveAtJst: "2026/05/16 09:00:00",
        activeDays7: 7,
        messageCount7d: 100,
        coverageRate: 1,
        badFeedbackRate: 0,
        activityLevel: "高アクティブ",
      },
    ],
  };
}

async function mockCurrentDashboardApis(page) {
  await page.route("**/api/metrics/system-dashboard?**", async (route) => {
    const url = new URL(route.request().url());
    await route.fulfill({ json: dashboardPayload(url.searchParams.get("preset") || "today") });
  });
  await page.route("**/api/metrics/users?**", async (route) => route.fulfill({ json: usersPayload() }));
  await page.route("**/api/metrics/users/1000001?**", async (route) =>
    route.fulfill({
      json: {
        user: { userId: "1000001", userEmail: "1000001@tc.terumo.co.jp" },
        summary: {
          activityLevel: "高アクティブ",
          messageCount: 42,
          answerSuccessRate: 0.95,
          lowCoverageRate: 0.06,
          badFeedbackRate: 0.02,
          followupCount: 8,
        },
        trend: [
          { date: "05-15", messageCount: 12, answerSuccessRate: 0.92, lowCoverageRate: 0.08 },
          { date: "05-16", messageCount: 18, answerSuccessRate: 0.96, lowCoverageRate: 0.04 },
        ],
        modeDistribution: [
          { label: "社内モード", count: 26, rate: 0.62 },
          { label: "Web検索モード", count: 16, rate: 0.38 },
        ],
        conversations: [
          {
            conversationId: "conv-001",
            title: "医療機器の価格確認と社内資料の確認",
            messageCount: 11,
            updatedAtJst: "2026/05/16 09:30:00",
          },
        ],
      },
    }),
  );
  await page.route("**/api/trace/messages?**", async (route) =>
    route.fulfill({
      json: {
        messages: [
          {
            timestampJst: "2026/05/16 09:20:00",
            roleLabel: "ユーザー",
            contentPreview: "この製品の価格を確認してください。",
            modeAtSendLabel: "社内モード",
            deviceLabel: "PC",
            coverageRate: 0.94,
            feedback: "none",
          },
        ],
        page: { nextCursor: "" },
      },
    }),
  );
}

test("current dashboard renders stable operational monitor", async ({ page }) => {
  await mockCurrentDashboardApis(page);
  await page.goto("/dashboard");

  await expect(page.locator("#sectionKpi")).toContainText("KPIサマリー");
  await expect(page.locator("#kpiCardsPrimary .kpiCard")).toHaveCount(5);
  await expect(page.locator("#kpiCardsPrimary")).not.toContainText("総リクエスト数");
  await expect(page.locator("#sectionEnvironment")).toContainText("利用環境・モード分析");
  await expect(page.locator("#sectionUsageTrend")).toContainText("利用推移");
  await expect(page.locator("#sectionActivity")).toContainText("活性度分布");
  await expect(page.locator("#sectionUsers")).toContainText("ユーザー一覧");
  await expect(page.locator("#sectionAnswerQuality")).toContainText("回答品質分析");
  await expect(page.locator("#sectionFollowup")).toContainText("連続質問分析");

  await expect(page.locator("#activityLegend")).toContainText("14.33%（1,842）");
  await expect(page.locator("#usersTable tbody tr")).toHaveCount(1);
  await expect(page.locator("#usersTable")).not.toContainText("unknown");
  await expect(page.locator("#usersTable")).not.toContainText("lcs-agent");

  const canvasCount = await page.locator("canvas").count();
  expect(canvasCount).toBeGreaterThanOrEqual(7);

  for (let i = 0; i < 10; i += 1) {
    await page.getByRole("button", { name: "再読込" }).click();
  }
  await expect(page.locator("#loadingStatus")).toContainText("表示中");
});

test("user detail route lazy-loads conversations and messages", async ({ page }) => {
  await mockCurrentDashboardApis(page);
  await page.goto("/dashboard?user_id=1000001");

  await expect(page.locator("#dashboardView")).toBeHidden();
  await expect(page.locator("#userDetailView")).toBeVisible();
  await expect(page.locator("#userDetailView")).toContainText("ユーザー詳細");
  await expect(page.locator("#conversationTable")).toContainText("conv-001");

  await page.locator(".conversationRow").first().click();
  await expect(page.locator("#messagesTable")).toContainText("この製品の価格を確認してください。");
  await expect(page.locator("#messagesTable")).toContainText("根拠カバレッジ率");

  await page.locator("#dashboardPreset").selectOption("last_7d");
  await expect(page.locator("#userDetailView")).toBeVisible();
  await expect(page.locator("#dashboardView")).toBeHidden();
});
