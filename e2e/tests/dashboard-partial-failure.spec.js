const { test, expect } = require("@playwright/test");

async function mockApisWithUsageFailure(page) {
  await page.route("**/api/metrics/overview?**", async (route) =>
    route.fulfill({
      json: {
        days: 1,
        overview: {
          request_count: 120,
          error_5xx_count: 2,
          error_5xx_rate: 0.016,
          request_p95_latency_ms: 240,
          qs_stable_rate: 0.92,
          first_answer_avg_ms: 810,
          enhance_answer_avg_ms: 1120,
        },
        usage: {
          days: 1,
          dau: 8,
          wau: 22,
          conversationCount: 35,
          messageCount: 220,
          feedbackLikeRate: 0.77,
        },
      },
    }),
  );

  await page.route("**/api/metrics/usage?**", async (route) =>
    route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ detail: "usage query failed: test fixture" }),
    }),
  );

  await page.route("**/api/metrics/errors?**", async (route) =>
    route.fulfill({
      json: {
        trend: [
          { bucket_label: "09:00", error_5xx_count: 1 },
          { bucket_label: "09:30", error_5xx_count: 0 },
        ],
        topEndpoints: [{ endpoint: "/api/metrics/usage", error_5xx_count: 2 }],
        topErrors: [{ error_type: "RuntimeError", count: 2 }],
      },
    }),
  );

  await page.route("**/api/metrics/devices?**", async (route) =>
    route.fulfill({
      json: {
        devices: [
          { device_class: "desktop", request_count: 90, error_5xx_count: 1, error_5xx_rate: 0.011, p95_latency_ms: 210 },
          { device_class: "mobile", request_count: 30, error_5xx_count: 1, error_5xx_rate: 0.033, p95_latency_ms: 290 },
        ],
      },
    }),
  );

  await page.route("**/api/metrics/query-suggest?**", async (route) =>
    route.fulfill({
      json: {
        logs: {
          stages: [
            { stage: "stable", count: 90, avg_latency_ms: 250, avg_suggestion_count: 4 },
            { stage: "degraded", count: 10, avg_latency_ms: 430, avg_suggestion_count: 3 },
          ],
          fallbackSources: [{ fallback_source: "local", reason: "timeout", count: 3 }],
        },
        facts: {
          impressions: 200,
          clicks: 40,
          adoptions: 14,
          clickRate: 0.2,
          adoptionRate: 0.35,
        },
      },
    }),
  );

  await page.route("**/api/history/users?**", async (route) =>
    route.fulfill({ json: { count: 0, users: [] } }),
  );
}

test("dashboard keeps rendering when one metrics module fails", async ({ page }) => {
  await mockApisWithUsageFailure(page);
  await page.goto("/dashboard");

  await expect(page.locator("#kpiCardsPrimary .card")).toHaveCount(6);
  await expect(page.locator("#kpiCardsSecondary .card")).toHaveCount(4);
  await expect(page.locator("#topEndpointsTable tbody tr")).toContainText("/api/metrics/usage");
  await expect(page.locator("#usageChart")).toBeVisible();
  await expect(page.locator("#systemUsageChart")).toBeVisible();
  await expect(page.locator("#toast")).toContainText("一部データの取得に失敗しました");
});
