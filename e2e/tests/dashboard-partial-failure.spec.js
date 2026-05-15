const { test, expect } = require("@playwright/test");

function minimalDashboardPayload() {
  return {
    meta: {
      generatedAt: "2026-05-16T01:00:00Z",
      fetchMs: 55,
      metricStatus: { answerSuccessRate: "proxy" },
    },
    kpis: {
      activeUserCount: 8,
      answerSuccessRate: 0.88,
      lowCoverageRate: 0.11,
      errorRate: 0.02,
      p95LatencyMs: 1480,
    },
    usageTrend: [{ date: "05-16", activeUserCount: 8, messageCount: 42 }],
    activityDistribution: {
      totalUserCount: 12,
      segments: [
        { label: "高アクティブ", count: 2, rate: 0.1667, activityKey: "high" },
        { label: "中アクティブ", count: 4, rate: 0.3333, activityKey: "middle" },
        { label: "低アクティブ", count: 3, rate: 0.25, activityKey: "low" },
        { label: "休眠ユーザー", count: 3, rate: 0.25, activityKey: "dormant" },
      ],
    },
    environmentMode: {
      requestByHour: Array.from({ length: 24 }, (_, hour) => ({
        hour: `${String(hour).padStart(2, "0")}:00`,
        requestCount: hour,
      })),
      deviceDistribution: [{ label: "PC", count: 42, rate: 1 }],
      modeDistribution: [{ label: "社内モード", count: 42, rate: 1 }],
    },
    answerQuality: {
      answerability: [{ label: "fully_answerable", count: 8, rate: 1 }],
      usability: [{ label: "ready", count: 8, rate: 1 }],
      deliveryReadiness: [{ label: "ready", count: 8, rate: 1 }],
      evidenceSufficiency: [{ label: "sufficient", count: 8, rate: 1 }],
    },
    followup: {
      recognizedCount: 4,
      successCount: 3,
      successRate: 0.75,
      explicitCorrectionCount: 1,
      clarificationRequiredCount: 0,
    },
  };
}

test("dashboard keeps visible partial data when one module API fails", async ({ page }) => {
  await page.route("**/api/metrics/system-dashboard?**", async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("preset") === "last_7d") {
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "test usage/activity failure" }),
      });
      return;
    }
    await route.fulfill({ json: minimalDashboardPayload() });
  });
  await page.route("**/api/metrics/users?**", async (route) =>
    route.fulfill({
      json: {
        users: [
          {
            userId: "1000002",
            userEmail: "1000002@tc.terumo.co.jp",
            lastActiveAtJst: "2026/05/16 08:00:00",
            activeDays7: 2,
            messageCount7d: 6,
            coverageRate: 0.9,
            badFeedbackRate: 0,
            activityLevel: "中アクティブ",
          },
        ],
      },
    }),
  );

  await page.goto("/dashboard");
  await expect(page.locator("#sectionKpi")).toContainText("回答成功率");
  await expect(page.locator("#sectionEnvironment")).toContainText("利用環境・モード分析");
  await expect(page.locator("#sectionUsers")).toContainText("1000002@tc.terumo.co.jp");
  await expect(page.locator("#loadingStatus")).toContainText("一部表示中");
});
