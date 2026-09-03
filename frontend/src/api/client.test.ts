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
});
