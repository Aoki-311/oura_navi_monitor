const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "./tests",
  timeout: 180000,
  expect: { timeout: 10000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.MONITOR_E2E_BASE_URL || "http://127.0.0.1:8099",
    extraHTTPHeaders: {
      "x-monitor-admin-email": process.env.MONITOR_E2E_ADMIN_EMAIL || "2401145@tc.terumo.co.jp",
    },
    headless: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    viewport: { width: 1440, height: 960 },
  },
});
