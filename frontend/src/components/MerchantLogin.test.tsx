import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ConnectionProvider, useConnection } from "../app/ConnectionContext";
import { ConnectionScreen } from "./ConnectionScreen";

const mocks = vi.hoisted(() => ({
  aal: "aal1",
  signIn: vi.fn(), verify: vi.fn(), enroll: vi.fn(), signOut: vi.fn(),
  create: vi.fn(),
}));
vi.mock("@supabase/supabase-js", () => ({ createClient: mocks.create }));

function Harness() {
  const { connected, disconnect } = useConnection();
  return connected ? <><p>Merchant workspace</p><button onClick={disconnect}>Sign out</button></> : <ConnectionScreen />;
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear(); vi.clearAllMocks(); mocks.aal = "aal1";
  mocks.signIn.mockResolvedValue({ error: null });
  mocks.verify.mockImplementation(async () => { mocks.aal = "aal2"; return { error: null }; });
  mocks.signOut.mockResolvedValue({ error: null });
  mocks.create.mockReturnValue({ auth: {
    signInWithPassword: mocks.signIn,
    getSession: async () => ({ data: { session: { access_token: "memory-only-token" } }, error: null }),
    signOut: mocks.signOut,
    onAuthStateChange: () => ({ data: { subscription: { unsubscribe: vi.fn() } } }),
    mfa: {
      listFactors: async () => ({ data: { totp: [{ id: "factor-one", status: "verified" }], all: [] }, error: null }),
      challengeAndVerify: mocks.verify, enroll: mocks.enroll,
    },
  } });
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const path = new URL(String(input)).pathname;
    if (!["/auth/config", "/demo/status", "/health"].includes(path)) {
      expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer memory-only-token");
      expect(new Headers(init?.headers).has("X-API-Key")).toBe(false);
      expect(init?.redirect).toBe("error");
    }
    const payload = path === "/auth/config" ? { mode: "supabase", url: "https://fixture.supabase.co", publishable_key: "sb_publishable_fixture" } :
      path === "/demo/status" ? { enabled: false } :
      path === "/auth/me" ? { user_id: "00000000-0000-4000-8000-000000000001", aal: mocks.aal, platform_admin: false, memberships: { merchant_a: "owner" } } :
      path === "/stats" ? { total_disputes_processed: 0, decisions: { FIGHT: 0, ACCEPT: 0, ESCALATE_DEGRADED: 0 }, win_rate: null, average_expected_value: null, evidence_collection_degraded_count: 0 } :
      path === "/auth/session/revoke" ? { status: "revoked" } : { status: "ok", model_loaded: true, stub_mode: false };
    return new Response(JSON.stringify(payload), { headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(() => vi.unstubAllGlobals());

it("requires MFA before connecting and revokes the backend session before sign out", async () => {
  render(<ConnectionProvider><Harness /></ConnectionProvider>);
  await userEvent.type(await screen.findByLabelText("Email"), "owner@example.invalid");
  await userEvent.type(screen.getByLabelText("Password"), "fixture-password");
  expect(screen.queryByLabelText("API key", { exact: true })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  await userEvent.type(await screen.findByLabelText("Authenticator code"), "123456");
  expect(screen.queryByText("Merchant workspace")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Verify MFA and connect" }));
  expect(await screen.findByText("Merchant workspace")).toBeInTheDocument();
  expect(mocks.verify).toHaveBeenCalledWith({ factorId: "factor-one", code: "123456" });
  expect(mocks.enroll).not.toHaveBeenCalled();
  expect(mocks.create).toHaveBeenCalledWith("https://fixture.supabase.co", "sb_publishable_fixture", {
    auth: { persistSession: false, autoRefreshToken: true, detectSessionInUrl: false },
  });
  expect(JSON.stringify(sessionStorage) + JSON.stringify(localStorage)).not.toContain("memory-only-token");
  await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
  await screen.findByLabelText("Email");
  const fetchMock = vi.mocked(fetch);
  const revokeIndex = fetchMock.mock.calls.findIndex(([url]) => String(url).endsWith("/auth/session/revoke"));
  expect(revokeIndex).toBeGreaterThanOrEqual(0);
  expect(fetchMock.mock.invocationCallOrder[revokeIndex]).toBeLessThan(mocks.signOut.mock.invocationCallOrder[0]);
});
