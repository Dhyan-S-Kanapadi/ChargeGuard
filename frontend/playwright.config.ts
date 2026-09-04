import { defineConfig, devices } from "@playwright/test";

const requestedPort = Number(process.env.CHARGEGUARD_E2E_PORT ?? "8765");
const port = Number.isInteger(requestedPort) && requestedPort >= 1024 && requestedPort <= 65535
  ? requestedPort
  : 8765;
const apiOrigin = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  retries: 0,
  reporter: "line",
  use: {
    baseURL: process.env.CHARGEGUARD_E2E_URL ?? `${apiOrigin}/dashboard/`,
    trace: "retain-on-failure",
  },
  webServer: {
    command: `py -m uvicorn main:app --host 127.0.0.1 --port ${port}`,
    cwd: "..",
    url: `${apiOrigin}/health`,
    reuseExistingServer: true,
    timeout: 30_000,
    env: {
      API_KEY: "chargeguard-e2e-key",
      ENVIRONMENT: "development",
      CHARGEGUARD_USE_STUBS: "true",
      RAZORPAY_WEBHOOK_ENABLED: "true",
      RAZORPAY_WEBHOOK_SECRET: "chargeguard-e2e-webhook-secret",
      RAZORPAY_SIMULATOR_ENABLED: "true",
      RAZORPAY_RECOVER_PENDING_ON_STARTUP: "false",
      RAZORPAY_SIMULATOR_TARGET_URL: `${apiOrigin}/webhook/razorpay`,
    },
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 } } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
});
