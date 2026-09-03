import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiClient, ApiError } from "../api/client";
import type { Health } from "../api/schemas";

const SESSION_KEY = "chargeguard.connection.v1";

type Connection = {
  baseUrl: string;
  apiKey: string;
  rememberForTab: boolean;
};

type ConnectionContextValue = Connection & {
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
  const [selectedMerchantId, setSelectedMerchantId] = useState("");
  const restoreAttempted = useRef(false);
  const client = useMemo(
    () => new ApiClient(connection.baseUrl, connection.apiKey),
    [connection.apiKey, connection.baseUrl],
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
    setHealth(healthResult);
    setConnected(true);
    if (next.rememberForTab) sessionStorage.setItem(SESSION_KEY, JSON.stringify(next));
    else sessionStorage.removeItem(SESSION_KEY);
    return healthResult;
  };

  const disconnect = () => {
    sessionStorage.removeItem(SESSION_KEY);
    setConnection({ baseUrl: window.location.origin, apiKey: "", rememberForTab: false });
    setHealth(null);
    setConnected(false);
    setSelectedMerchantId("");
  };

  return (
    <ConnectionContext.Provider value={{
      ...connection,
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
