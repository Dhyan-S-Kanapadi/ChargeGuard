// @vitest-environment node
import { http, HttpResponse } from "msw";
import { beforeAll, describe, expect, it } from "vitest";
import { ApiClient, joinUrl } from "./client";
import { server } from "../test/server";

describe("ApiClient", () => {
  beforeAll(() => {
    Object.defineProperty(globalThis, "window", { configurable: true, value: { location: { origin: "http://localhost" }, setTimeout, clearTimeout } });
  });
  it("intercepts requests with MSW", async () => {
    server.use(http.get("http://localhost/probe", () => HttpResponse.json({ ok: true })));
    const response = await fetch("http://localhost/probe");
    expect(await response.json()).toEqual({ ok: true });
  });
  it("supports request headers", async () => {
    server.use(http.get("http://localhost/header-probe", ({ request }) => HttpResponse.json({ key: request.headers.get("X-API-Key") })));
    const response = await fetch("http://localhost/header-probe", { headers: new Headers({ "X-API-Key": "key" }) });
    expect(await response.json()).toEqual({ key: "key" });
  });
  it("joins paths beneath the selected HTTP origin", () => {
    expect(joinUrl("https://api.example.test/root", "/health")).toBe("https://api.example.test/root/health");
    expect(() => joinUrl("javascript:alert(1)", "/health")).toThrow("HTTP or HTTPS");
  });

  it("attaches the API key only to authenticated requests", async () => {
    const seen: Array<string | null> = [];
    server.use(
      http.get("http://localhost/health", ({ request }) => {
        seen.push(request.headers.get("X-API-Key"));
        return HttpResponse.json({ status: "ok", model_loaded: true, stub_mode: true });
      }),
      http.get("http://localhost/stats", ({ request }) => {
        seen.push(request.headers.get("X-API-Key"));
        return HttpResponse.json({ total_disputes_processed: 0, decisions: { FIGHT: 0, ACCEPT: 0, ESCALATE_DEGRADED: 0 }, win_rate: null, average_expected_value: null, evidence_collection_degraded_count: 0 });
      }),
    );
    const client = new ApiClient("http://localhost", "secret-test-key");
    await client.health(); await client.stats();
    expect(seen).toEqual([null, "secret-test-key"]);
  });

  it("rejects invalid response contracts", async () => {
    server.use(http.get("http://localhost/health", () => HttpResponse.json({ status: "invented" })));
    await expect(new ApiClient("http://localhost", "key").health()).rejects.toMatchObject({ status: 500 });
  });

  it("extracts FastAPI validation errors and Retry-After", async () => {
    server.use(http.post("http://localhost/assistant/query", () => HttpResponse.json({ detail: [{ loc: ["body", "question"], msg: "too short" }] }, { status: 429, headers: { "Retry-After": "7" } })));
    const request = new ApiClient("http://localhost", "key").askAssistant("question");
    await expect(request).rejects.toEqual(expect.objectContaining({ status: 429, retryAfter: 7, message: "body.question: too short" }));
  });

  it("sends suggestion approval through the authenticated classification endpoint", async () => {
    let body: unknown;
    let apiKey: string | null = null;
    server.use(http.post("http://localhost/disputes/disp_1/classification", async ({ request }) => {
      body = await request.json();
      apiKey = request.headers.get("X-API-Key");
      return HttpResponse.json({ chargeback_id: "disp_1", status: "scheduled", card_network: "VISA", network_reason_code: "10.4" });
    }));

    await new ApiClient("http://localhost", "secret-test-key").classifyDispute(
      "disp_1", "VISA", "10.4", "operator-1", "rcs_1",
    );

    expect(apiKey).toBe("secret-test-key");
    expect(body).toEqual({
      card_network: "VISA",
      network_reason_code: "10.4",
      actor_id: "operator-1",
      suggestion_id: "rcs_1",
    });
  });

  it("loads and runs validated simulator catalog scenarios", async () => {
    let runBody: unknown;
    server.use(
      http.get("http://localhost/dev/razorpay-simulator/scenarios", () => HttpResponse.json([{
        id: "webhook-invalid-signature",
        family: "Webhook trust",
        title: "Invalid signature",
        description: "Synthetic negative path",
        expected: "HTTP 401",
        behavior: "invalid_signature",
        payload: {
          payment_amount_paise: 1000,
          dispute_amount_paise: 1000,
          currency: "INR",
          method: "card",
          card_network: "VISA",
          network_reason_code: "13.1",
          razorpay_reason_code: "fraudulent",
          respond_within_hours: 72,
        },
      }])),
      http.post("http://localhost/dev/razorpay-simulator/scenarios/webhook-invalid-signature/run", async ({ request }) => {
        runBody = await request.json();
        return HttpResponse.json({
          scenario_id: "webhook-invalid-signature",
          dispute_id: "disp_SIM_1",
          order_seeded: true,
          expected: "HTTP 401",
          deliveries: [{
            event_id: "evt_SIM_1",
            event_name: "payment.dispute.created",
            delivery: { status_code: 401 },
            payload_sha256: "abc",
          }],
        });
      }),
    );
    const client = new ApiClient("http://localhost", "key");

    const catalog = await client.simulationScenarios();
    const result = await client.runSimulationScenario(catalog[0].id, "merchant_sim");

    expect(catalog[0].family).toBe("Webhook trust");
    expect(result.deliveries[0].delivery.status_code).toBe(401);
    expect(runBody).toEqual({ merchant_id: "merchant_sim" });
  });
});
