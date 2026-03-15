const { test, expect } = require("@playwright/test");

function buildUsageRows(days = 14) {
  const rows = [];
  for (let i = 0; i < days; i += 1) {
    const day = `2026-03-${String(i + 1).padStart(2, "0")}`;
    rows.push({
      day,
      device_class: "desktop",
      request_count: 80 + i,
      error_5xx_count: i % 3,
      error_5xx_rate: (i % 3) / (80 + i),
      p95_latency_ms: 180 + i * 2,
    });
    rows.push({
      day,
      device_class: "mobile",
      request_count: 45 + i,
      error_5xx_count: i % 2,
      error_5xx_rate: (i % 2) / (45 + i),
      p95_latency_ms: 210 + i * 2,
    });
  }
  return rows;
}

async function mockDashboardApis(page) {
  const overview = {
    days: 7,
    overview: {
      request_count: 2400,
      error_5xx_count: 22,
      error_5xx_rate: 0.009,
      request_p95_latency_ms: 250,
      qs_total: 500,
      qs_stable_count: 450,
      qs_degraded_count: 50,
      qs_stable_rate: 0.9,
      qs_avg_latency_ms: 320,
      qs_avg_suggestion_count: 4,
      restore_total: 120,
      restore_success_count: 118,
      restore_success_rate: 0.983,
    },
    usage: {
      days: 7,
      dau: 18,
      wau: 37,
      activeUsersInWindow: 37,
      conversationCount: 154,
      messageCount: 1288,
      usersScanned: 42,
    },
  };

  const errors = {
    days: 7,
    trend: [
      { day: "2026-03-01", error_5xx_count: 2 },
      { day: "2026-03-02", error_5xx_count: 1 },
      { day: "2026-03-03", error_5xx_count: 3 },
    ],
    topEndpoints: [{ endpoint: "/api/metrics/overview", error_5xx_count: 3 }],
    topErrors: [{ error_type: "TimeoutError", count: 5 }],
  };

  const devices = {
    days: 7,
    devices: [
      { device_class: "desktop", request_count: 1800, error_5xx_count: 16, error_5xx_rate: 0.0089, p95_latency_ms: 238 },
      { device_class: "mobile", request_count: 600, error_5xx_count: 6, error_5xx_rate: 0.01, p95_latency_ms: 276 },
    ],
  };

  const querySuggest = {
    days: 7,
    logs: {
      stages: [
        { stage: "stable", count: 450, avg_latency_ms: 290, avg_suggestion_count: 4 },
        { stage: "degraded", count: 50, avg_latency_ms: 420, avg_suggestion_count: 3 },
      ],
      fallbackSources: [{ fallback_source: "local", reason: "timeout", count: 14 }],
    },
    facts: {
      days: 7,
      impressions: 1200,
      clicks: 180,
      adoptions: 66,
      editAfterAccepts: 12,
      dismisses: 8,
      clickRate: 0.15,
      adoptionRate: 0.366,
      editAfterAcceptRate: 0.182,
    },
  };

  const users = {
    count: 2,
    users: [
      { userId: "u-1", userEmail: "a@example.com", updatedAt: "2026-03-01T00:00:00Z" },
      { userId: "u-2", userEmail: "b@example.com", updatedAt: "2026-03-02T00:00:00Z" },
    ],
  };

  const convs = {
    count: 1,
    conversations: [{ id: "c-1", title: "conv", updatedAt: "2026-03-02T00:00:00Z" }],
  };

  const messages = {
    conversationId: "c-1",
    messages: [{ id: "m-1", role: "user", timestamp: "2026-03-02T00:00:00Z", content: "hello" }],
  };

  await page.route("**/api/metrics/overview?**", async (route) => route.fulfill({ json: overview }));
  await page.route("**/api/metrics/usage?**", async (route) => route.fulfill({ json: { days: 7, timeseries: buildUsageRows(14), usage: overview.usage } }));
  await page.route("**/api/metrics/errors?**", async (route) => route.fulfill({ json: errors }));
  await page.route("**/api/metrics/devices?**", async (route) => route.fulfill({ json: devices }));
  await page.route("**/api/metrics/query-suggest?**", async (route) => route.fulfill({ json: querySuggest }));
  await page.route("**/api/history/users?**", async (route) => route.fulfill({ json: users }));
  await page.route("**/api/history/users/*/conversations?**", async (route) => route.fulfill({ json: convs }));
  await page.route("**/api/history/users/*/conversations/*?**", async (route) => route.fulfill({ json: messages }));
}

test("dashboard long-refresh remains stable (layout/charts)", async ({ page }) => {
  await mockDashboardApis(page);
  await page.goto("/dashboard");
  await page.waitForSelector("#usageChart");

  const initial = await page.evaluate(() => {
    const chartWrapHeights = Array.from(document.querySelectorAll(".chartWrap")).map((el) =>
      Math.round(el.getBoundingClientRect().height),
    );
    const chartInstances = window.Chart && window.Chart.instances
      ? Object.keys(window.Chart.instances).length
      : null;
    return {
      scrollHeight: document.documentElement.scrollHeight,
      canvasCount: document.querySelectorAll("canvas").length,
      chartWrapHeights,
      chartInstances,
    };
  });

  expect(initial.canvasCount).toBe(4);
  expect(initial.chartWrapHeights.length).toBe(4);
  for (const h of initial.chartWrapHeights) {
    expect(h).toBeGreaterThan(230);
    expect(h).toBeLessThan(420);
  }
  if (initial.chartInstances !== null) {
    expect(initial.chartInstances).toBeLessThanOrEqual(4);
  }

  for (let i = 0; i < 40; i += 1) {
    await page.getByRole("button", { name: "再読み込み" }).click();
    await page.waitForTimeout(20);
  }

  const after = await page.evaluate(() => {
    const chartWrapHeights = Array.from(document.querySelectorAll(".chartWrap")).map((el) =>
      Math.round(el.getBoundingClientRect().height),
    );
    const chartInstances = window.Chart && window.Chart.instances
      ? Object.keys(window.Chart.instances).length
      : null;
    return {
      scrollHeight: document.documentElement.scrollHeight,
      canvasCount: document.querySelectorAll("canvas").length,
      chartWrapHeights,
      chartInstances,
    };
  });

  expect(after.canvasCount).toBe(4);
  expect(after.chartWrapHeights.length).toBe(4);
  for (const h of after.chartWrapHeights) {
    expect(h).toBeGreaterThan(230);
    expect(h).toBeLessThan(420);
  }
  expect(after.scrollHeight).toBeLessThanOrEqual(initial.scrollHeight + 240);
  if (after.chartInstances !== null) {
    expect(after.chartInstances).toBeLessThanOrEqual(4);
  }
});
