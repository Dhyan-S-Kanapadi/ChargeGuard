import { useQuery } from "@tanstack/react-query";
import {
  Bot, Building2, ChevronDown, CircleHelp, Gauge, Menu, Moon, Search,
  Settings, ShieldCheck, SlidersHorizontal, Sun, TestTube2, X,
} from "lucide-react";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useConnection } from "../app/ConnectionContext";
import type { Route } from "../app/useHashRoute";
import { Button } from "./ui";

const navItems: Array<{ route: Route; label: string; icon: typeof Gauge; protected?: boolean }> = [
  { route: "overview", label: "Overview", icon: Gauge },
  { route: "disputes", label: "Disputes", icon: ShieldCheck },
  { route: "operations", label: "Operations", icon: SlidersHorizontal, protected: true },
  { route: "ai", label: "Guard AI", icon: Bot },
  { route: "merchants", label: "Merchants", icon: Building2 },
  { route: "settings", label: "Settings", icon: Settings },
];

export function AppShell({ route, children }: { route: Route; children: ReactNode }) {
  const { client, health, selectedMerchantId, setSelectedMerchantId, disconnect, isDemo } = useConnection();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [dark, setDark] = useState(() => localStorage.getItem("chargeguard.theme") === "dark");
  const merchants = useQuery({ queryKey: ["merchants"], queryFn: ({ signal }) => client.merchants(signal) });
  const simulator = useQuery({
    queryKey: ["simulator-availability"],
    queryFn: ({ signal }) => client.simulatorDisputes(signal),
    retry: false,
    staleTime: 60_000,
  });

  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    localStorage.setItem("chargeguard.theme", dark ? "dark" : "light");
  }, [dark]);

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    window.location.hash = `#/disputes${search ? `?q=${encodeURIComponent(search)}` : ""}`;
    setDrawerOpen(false);
  };

  const items = simulator.isSuccess
    ? [...navItems.slice(0, 5), { route: "simulator" as Route, label: "Simulator", icon: TestTube2 }, ...navItems.slice(5)]
    : navItems;

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <aside className={`sidebar ${drawerOpen ? "sidebar--open" : ""}`} aria-label="Primary navigation">
        <div className="brand"><span className="brand__mark"><ShieldCheck /></span><span><strong>ChargeGuard</strong><small>Dispute intelligence</small></span><button className="icon-button sidebar__close" onClick={() => setDrawerOpen(false)} aria-label="Close navigation"><X /></button></div>
        <nav>
          {items.filter(item => !isDemo || ["overview", "disputes", "ai", "simulator"].includes(item.route)).map(({ route: itemRoute, label, icon: Icon, protected: isProtected }) => (
            <a key={itemRoute} href={`#/${itemRoute}`} className={route === itemRoute ? "active" : ""} onClick={() => setDrawerOpen(false)} aria-current={route === itemRoute ? "page" : undefined}>
              <Icon aria-hidden="true" /><span>{label}</span>{isProtected ? <small>Protected</small> : null}{itemRoute === "simulator" ? <small>Dev</small> : null}
            </a>
          ))}
        </nav>
        <div className="sidebar__foot"><CircleHelp aria-hidden="true" /><span><strong>Operator workspace</strong><small>API-key service access</small></span></div>
      </aside>
      {drawerOpen ? <button className="scrim" onClick={() => setDrawerOpen(false)} aria-label="Close navigation" /> : null}
      <div className="workspace">
        <header className="topbar">
          <button className="icon-button menu-button" onClick={() => setDrawerOpen(true)} aria-label="Open navigation"><Menu /></button>
          <form className="global-search" role="search" onSubmit={submitSearch}>
            <Search aria-hidden="true" /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search chargebacks" aria-label="Search chargebacks" />
          </form>
          <div className="topbar__actions">
            <label className="workspace-select"><span className="sr-only">Merchant workspace</span><Building2 aria-hidden="true" /><select value={selectedMerchantId} onChange={(event) => setSelectedMerchantId(event.target.value)}><option value="">All merchants</option>{merchants.data?.map((merchant) => <option key={merchant.merchant_id} value={merchant.merchant_id}>{merchant.name}</option>)}</select><ChevronDown aria-hidden="true" /></label>
            <span className={`health-chip health-chip--${health?.status ?? "degraded"}`}><span />{health?.status === "ok" ? "Systems healthy" : "Health degraded"}</span>
            <button className="icon-button" onClick={() => setDark((value) => !value)} aria-label={`Switch to ${dark ? "light" : "dark"} theme`}>{dark ? <Sun /> : <Moon />}</button>
            <Button variant="ghost" onClick={disconnect}>Disconnect</Button>
          </div>
        </header>
        <main id="main-content" className="content" tabIndex={-1}>{children}</main>
      </div>
    </div>
  );
}
