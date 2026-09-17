import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiClient, ApiError } from "../api/client";
import type { Health } from "../api/schemas";
import type { SupabaseClient } from "@supabase/supabase-js";
import { merchantApi } from "./supabase";

const SESSION_KEY = "chargeguard.connection.v1";

type Connection = {
  baseUrl: string;
  apiKey: string;
  rememberForTab: boolean;
};

type ConnectionContextValue = Connection & {
  isDemo: boolean;
  startDemo: () => Promise<void>;
  connectSupabase: (auth: SupabaseClient) => Promise<void>;
  client: ApiClient;
  connected: boolean;
  health: Health | null;
  selectedMerchantId: string;
  setSelectedMerchantId: (id: string) => void;
  connect: (next: Connection) => Promise<Health>;
  disconnect: () => void;
};

function initialConnection(): Connection {
  try {
    const stored = sessionStorage.getItem(SESSION_KEY);
    if (stored) {
      const value = JSON.parse(stored) as Partial<Connection>;
      if (typeof value.baseUrl === "string" && typeof value.apiKey === "string") {
        return { baseUrl: value.baseUrl, apiKey: value.apiKey, rememberForTab: true };
      }
    }
  } catch {
    sessionStorage.removeItem(SESSION_KEY);
  }
  return { baseUrl: window.location.origin, apiKey: "", rememberForTab: false };
}

const ConnectionContext = createContext<ConnectionContextValue | null>(null);

export function ConnectionProvider({ children }: { children: ReactNode }) {
  const [connection, setConnection] = useState(initialConnection);
  const [health, setHealth] = useState<Health | null>(null);
  const [connected, setConnected] = useState(false);
  const [demoToken, setDemoToken] = useState("");
  const [supabase, setSupabase] = useState<SupabaseClient | null>(null);
  const [selectedMerchantId, setSelectedMerchantId] = useState("");
  const restoreAttempted = useRef(false);
  const client = useMemo(
    () => supabase ? merchantApi(supabase) : new ApiClient(connection.baseUrl, connection.apiKey, 12_000, demoToken),
    [connection.apiKey, connection.baseUrl, demoToken, supabase],
  );

  useEffect(() => {
    if (restoreAttempted.current || connected || !connection.rememberForTab || !connection.apiKey) return;
    restoreAttempted.current = true;
    let active = true;
    Promise.all([client.health(), client.stats()]).then(([healthResult]) => {
      if (!active) return;
      setHealth(healthResult); setConnected(true);
    }).catch(() => {
      if (!active) return;
      sessionStorage.removeItem(SESSION_KEY);
      setConnection({ baseUrl: window.location.origin, apiKey: "", rememberForTab: false });
    });
    return () => { active = false; };
  }, [client, connected, connection.apiKey, connection.rememberForTab]);

  const connect = async (next: Connection) => {
    const candidate = new ApiClient(next.baseUrl, next.apiKey);
    const healthResult = await candidate.health();
    try {
      await candidate.stats();
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) throw error;
      throw error;
    }
    setConnection(next);
    setDemoToken("");
    setHealth(healthResult);
    setConnected(true);
    if (next.rememberForTab) sessionStorage.setItem(SESSION_KEY, JSON.stringify(next));
    else sessionStorage.removeItem(SESSION_KEY);
    return healthResult;
  };

  const startDemo = async () => {
    const baseUrl = window.location.origin;
    const candidate = new ApiClient(baseUrl, "");
    const healthResult = await candidate.health();
    const session = await candidate.startDemo();
    sessionStorage.removeItem(SESSION_KEY);
    setConnection({ baseUrl, apiKey: "", rememberForTab: false });
    setDemoToken(session.session_token);
    setHealth(healthResult);
    setSelectedMerchantId("merchant_reviewer_demo");
    setConnected(true);
    window.location.hash = "#/simulator";
  };

  const connectSupabase = async (auth: SupabaseClient) => {
    const candidate = merchantApi(auth);
    const user = await candidate.currentUser();
    if (user.platform_admin) throw new Error("Platform administration uses the protected admin API, not the merchant workspace.");
    await candidate.stats();
    setHealth(await candidate.health());
    sessionStorage.removeItem(SESSION_KEY);
    setConnection({ baseUrl: window.location.origin, apiKey: "", rememberForTab: false });
    setDemoToken("");
    setSupabase(auth);
    setSelectedMerchantId(Object.keys(user.memberships)[0] ?? "");
    setConnected(true);
  };

  useEffect(() => {
    if (!supabase) return;
    const { data } = supabase.auth.onAuthStateChange((event) => {
      if (event === "SIGNED_OUT") { setConnected(false); setSupabase(null); }
    });
    return () => data.subscription.unsubscribe();
  }, [supabase]);

  const disconnect = async () => {
    if (supabase) {
      // Keep the session visible if durable revocation fails; do not claim logout succeeded.
      try { await client.revokeSession(); }
      catch (error) {
        if (!(error instanceof ApiError && [401, 403].includes(error.status))) {
          window.alert("Logout could not revoke your session. Please retry; contact your administrator if it persists."); return;
        }
      }
      await supabase.auth.signOut({ scope: "local" });
      setSupabase(null);
    }
    setDemoToken("");
    sessionStorage.removeItem(SESSION_KEY);
    setConnection({ baseUrl: window.location.origin, apiKey: "", rememberForTab: false });
    setHealth(null);
    setConnected(false);
    setSelectedMerchantId("");
  };

  return (
    <ConnectionContext.Provider value={{
      ...connection,
      isDemo: Boolean(demoToken),
      startDemo,
      connectSupabase,
      client,
      connected,
      health,
      selectedMerchantId,
      setSelectedMerchantId,
      connect,
      disconnect,
    }}>
      {children}
    </ConnectionContext.Provider>
  );
}

export function useConnection() {
  const value = useContext(ConnectionContext);
  if (!value) throw new Error("useConnection must be used inside ConnectionProvider.");
  return value;
}
