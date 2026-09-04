import { expect, test } from "@playwright/test";

test("operator workflow works without browser errors", async ({ page, request }, testInfo) => {
  test.setTimeout(60_000);
  const apiOrigin = `http://127.0.0.1:${process.env.CHARGEGUARD_E2E_PORT ?? "8765"}`;
  const suffix = testInfo.project.name.replace(/\W/g, "_");
  const merchantId = `merchant_e2e_${suffix}`;
  const chargebackId = `cb_e2e_${suffix}`;
  const headers = { "X-API-Key": "chargeguard-e2e-key" };
  const merchant = await request.post(`${apiOrigin}/merchants`, { headers, data: { merchant_id: merchantId, name: `E2E ${suffix} Store`, vertical: "ecommerce", payment_provider: "razorpay", razorpay_account_id: `acc_e2e_${suffix}`, freshdesk_domain: "", average_order_value: 2500, chargeback_history_count: 0, transaction_volume_30d_by_network: { VISA: 1000 } } });
  expect([201, 409]).toContain(merchant.status());
  const chargeback = await request.post(`${apiOrigin}/webhook/chargeback`, { headers, data: { chargeback_id: chargebackId, reason_code: "10.4", card_network: "VISA", dispute_amount: 1499, currency: "INR", filing_deadline: new Date(Date.now() + 72 * 60 * 60 * 1000).toISOString(), merchant_id: merchantId, order_id: `order_${suffix}`, payment_id: `pay_${suffix}` } });
  expect([202, 409]).toContain(chargeback.status());

  const browserErrors: string[] = [];
  page.on("console", message => { if (message.type() === "error") browserErrors.push(message.text()); });
  await page.goto("./");
  await page.getByLabel("API key", { exact: true }).fill("chargeguard-e2e-key");
  await page.getByRole("button", { name: "Test and connect" }).click();
  await expect(page.getByRole("heading", { name: "Operational clarity, case by case." })).toBeVisible();

  if (testInfo.project.name === "mobile") await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("link", { name: "Disputes" }).click();
  await page.getByLabel("Search", { exact: true }).fill(chargebackId);
  const caseControl = testInfo.project.name === "mobile" ? page.locator(".case-cards button").filter({ hasText: chargebackId }) : page.getByRole("button", { name: chargebackId });
  await expect(caseControl).toBeVisible();
  await caseControl.click();
  await expect(page.getByRole("heading", { name: chargebackId })).toBeVisible();
  await page.getByRole("complementary", { name: `Case ${chargebackId}` }).getByRole("button", { name: "Close case" }).click();

  if (testInfo.project.name === "mobile") await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("link", { name: "Operations" }).click();
  await page.getByRole("button", { name: "Process pending (max 25)" }).click();
  await page.getByRole("button", { name: "Confirm operation" }).click();
  await expect(page.getByText("Pending-event recovery was accepted.")).toBeVisible();

  if (testInfo.project.name === "mobile") await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("link", { name: "Simulator" }).click();
  const simulatorMerchant = page.getByLabel("Mapped Razorpay merchant");
  await simulatorMerchant.selectOption(merchantId);
  await expect(simulatorMerchant).toHaveValue(merchantId);
  const [simulationResponse] = await Promise.all([
    page.waitForResponse(response => response.url().includes("/dev/razorpay-simulator/scenarios/") && response.url().endsWith("/run") && response.request().method() === "POST"),
    page.getByRole("button", { name: "Run selected scenario" }).click(),
  ]);
  expect(simulationResponse.status(), await simulationResponse.text()).toBe(200);
  await expect(page.getByText(/Scenario .* ran as .* HTTP delivery status: 202/)).toBeVisible({ timeout: 20_000 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: testInfo.outputPath("workspace.png"), fullPage: true });
  expect(browserErrors).toEqual([]);
});
