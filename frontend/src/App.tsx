import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { ConnectionProvider, useConnection } from "./app/ConnectionContext";
import { useHashRoute } from "./app/useHashRoute";
import { AppShell } from "./components/AppShell";
import { ConnectionScreen } from "./components/ConnectionScreen";
import { AssistantPage } from "./features/assistant/AssistantPage";
import { DisputesPage } from "./features/disputes/DisputesPage";
import { MerchantsPage } from "./features/merchants/MerchantsPage";
import { OperationsPage } from "./features/operations/OperationsPage";
import { OverviewPage } from "./features/overview/OverviewPage";
import { SettingsPage } from "./features/settings/SettingsPage";
import { SimulatorPage } from "./features/simulator/SimulatorPage";

function Workspace() { const { connected } = useConnection(); const route = useHashRoute(); if (!connected) return <ConnectionScreen />; const pages = { overview: <OverviewPage />, disputes: <DisputesPage />, operations: <OperationsPage />, ai: <AssistantPage />, merchants: <MerchantsPage />, simulator: <SimulatorPage />, settings: <SettingsPage /> }; return <AppShell route={route}>{pages[route]}</AppShell>; }
export default function App() { const [queryClient] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: (count, error) => !navigator.onLine ? false : count < 1 && !(error && typeof error === "object" && "status" in error && [401,403,404,409,422,429].includes(Number(error.status))), refetchOnWindowFocus: true, refetchOnReconnect: true }, mutations: { retry: false } } })); return <QueryClientProvider client={queryClient}><ConnectionProvider><Workspace /></ConnectionProvider></QueryClientProvider>; }
