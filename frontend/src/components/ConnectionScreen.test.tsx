import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConnectionProvider, useConnection } from "../app/ConnectionContext";
import { ConnectionScreen } from "./ConnectionScreen";

const stats = { total_disputes_processed: 0, decisions: { FIGHT: 0, ACCEPT: 0, ESCALATE_DEGRADED: 0 }, win_rate: null, average_expected_value: null, evidence_collection_degraded_count: 0 };
function Harness() { return useConnection().connected ? <p>Connected workspace</p> : <ConnectionScreen />; }

describe("connection screen", () => {
  beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
  afterEach(() => vi.unstubAllGlobals());

  it("connects without persisting the key by default", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request) => new Response(JSON.stringify(String(input).endsWith("/health") ? { status: "ok", model_loaded: true, stub_mode: true } : stats), { headers: { "Content-Type": "application/json" } })));
    render(<ConnectionProvider><Harness /></ConnectionProvider>);
    await userEvent.type(screen.getByLabelText("API key", { exact: true }), "temporary-key");
    await userEvent.click(screen.getByRole("button", { name: "Test and connect" }));
    expect(await screen.findByText("Connected workspace")).toBeInTheDocument();
    expect(sessionStorage.getItem("chargeguard.connection.v1")).toBeNull();
    expect(JSON.stringify(localStorage)).not.toContain("temporary-key");
  });

  it("reports a rejected API key", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request) => String(input).endsWith("/health") ? new Response(JSON.stringify({ status: "degraded", model_loaded: false, stub_mode: true }), { headers: { "Content-Type": "application/json" } }) : new Response(JSON.stringify({ detail: "Missing or invalid API key." }), { status: 401, headers: { "Content-Type": "application/json" } })));
    render(<ConnectionProvider><Harness /></ConnectionProvider>);
    await userEvent.type(screen.getByLabelText("API key", { exact: true }), "invalid-key");
    await userEvent.click(screen.getByRole("button", { name: "Test and connect" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("API key was rejected");
  });

  it("enters public demo without storing or sending an operator key", async () => {
    const calls: Array<{url: string; headers: Headers}> = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, headers: new Headers(init?.headers) });
      const body = url.endsWith("/demo/status") ? { enabled: true } :
        url.endsWith("/demo/session") ? { session_token: "restricted-demo-token", expires_in: 3600 } :
        { status: "ok", model_loaded: true, stub_mode: true };
      return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
    }));
    render(<ConnectionProvider><Harness /></ConnectionProvider>);
    await userEvent.click(await screen.findByRole("button", { name: "Try Demo" }));
    expect(await screen.findByText("Connected workspace")).toBeInTheDocument();
    expect(calls.every(call => !call.headers.has("X-API-Key"))).toBe(true);
    expect(calls.find(call => call.url.endsWith("/demo/session"))?.headers.get("X-Demo-Request")).toBe("1");
    expect(JSON.stringify(sessionStorage) + JSON.stringify(localStorage)).not.toContain("restricted-demo-token");
  });
});
