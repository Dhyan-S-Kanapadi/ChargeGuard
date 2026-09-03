import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, CircleDollarSign, Clock3, FileWarning, ShieldCheck, Target } from "lucide-react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { ReactNode } from "react";
import { useConnection } from "../../app/ConnectionContext";
import { Badge, EmptyState, ErrorState, Panel, Skeleton } from "../../components/ui";
import { deadlineLabel, formatDate, formatMoney, formatPercent, recommendationTone } from "../../utils/format";
import { rankActionRadar, sumByCurrency } from "./overview";

export function OverviewPage() {
  const { client, selectedMerchantId } = useConnection();
  const stats = useQuery({ queryKey: ["stats"], queryFn: ({ signal }) => client.stats(signal), refetchInterval: 30_000 });
  const disputesQuery = useQuery({ queryKey: ["disputes", "details"], queryFn: ({ signal }) => client.disputes(signal), refetchInterval: 30_000 });
  if (stats.isLoading || disputesQuery.isLoading) return <><PageHeading /><Skeleton lines={7} /></>;
  if (stats.error || disputesQuery.error) return <><PageHeading /><ErrorState error={stats.error ?? disputesQuery.error} retry={() => { void stats.refetch(); void disputesQuery.refetch(); }} /></>;
  const allDisputes = disputesQuery.data ?? [];
  const disputes = selectedMerchantId ? allDisputes.filter((item) => item.state.merchant_profile.merchant_id === selectedMerchantId) : allDisputes;
  const computedStats = selectedMerchantId ? {
    total_disputes_processed: disputes.length,
    decisions: {
      FIGHT: disputes.filter((item) => item.state.decision === "FIGHT").length,
      ACCEPT: disputes.filter((item) => item.state.decision === "ACCEPT").length,
      ESCALATE_DEGRADED: disputes.filter((item) => item.state.decision === "ESCALATE_DEGRADED").length,
    },
    win_rate: (() => { const outcomes = disputes.filter((item) => ["WIN", "LOSS"].includes(item.state.final_outcome ?? "")); return outcomes.length ? outcomes.filter((item) => item.state.final_outcome === "WIN").length / outcomes.length : null; })(),
    evidence_collection_degraded_count: disputes.filter((item) => item.state.evidence_collection_degraded).length,
  } : stats.data!;
  const revenueAtRisk = sumByCurrency(disputes, (item) => item.state.dispute_amount);
  const expectedRecovery = sumByCurrency(disputes, (item) => item.state.expected_value === null ? null : Math.max(0, item.state.expected_value));
  const radar = rankActionRadar(disputes).slice(0, 5);
  const distribution = Object.entries(computedStats.decisions).map(([name, value]) => ({ name: name.replace("ESCALATE_DEGRADED", "ESCALATED"), value }));
  const coverage = disputes.length ? (disputes.length - computedStats.evidence_collection_degraded_count) / disputes.length : null;
  return (
    <>
      <PageHeading />
      <section className="metrics-grid" aria-label="Portfolio metrics">
        <Metric label="Revenue at risk" values={revenueAtRisk} icon={<CircleDollarSign />} />
        <Metric label="Expected recovery (EV)" values={expectedRecovery} icon={<Target />} />
        <Metric label="Adjudicated win rate" value={formatPercent(computedStats.win_rate)} helper={computedStats.win_rate === null ? "No filed outcomes yet" : "Filed WIN/LOSS only"} icon={<ShieldCheck />} />
        <Metric label="Evidence coverage" value={formatPercent(coverage)} helper={`${computedStats.evidence_collection_degraded_count} degraded`} icon={<FileWarning />} />
      </section>
      <section className="summary-strip">
        <span><strong>{computedStats.total_disputes_processed}</strong>Total disputes</span>
        <span><strong>{computedStats.decisions.FIGHT}</strong>FIGHT</span>
        <span><strong>{computedStats.decisions.ACCEPT}</strong>ACCEPT</span>
        <span><strong>{computedStats.decisions.ESCALATE_DEGRADED}</strong>Escalated</span>
      </section>
      <div className="overview-grid">
        <Panel title="Action Radar" action={<a href="#/disputes">Open workspace <ArrowRight size={14} /></a>}>
          {radar.length ? <ol className="radar-list">{radar.map((item, index) => {
            const deadline = deadlineLabel(item.state.filing_deadline);
            return <li key={item.chargeback_id}><span className="radar-rank">{String(index + 1).padStart(2, "0")}</span><div><a href={`#/disputes?case=${encodeURIComponent(item.chargeback_id)}`} className="case-link">{item.chargeback_id}</a><small>{item.state.merchant_profile.name} · {item.state.provider_reason_code || item.state.reason_code || "Reason unavailable"}</small></div><div className="radar-amount">{formatMoney(item.state.dispute_amount, item.state.currency)}<Badge tone={recommendationTone(item.state.decision)}>{item.state.decision ?? "PENDING"}</Badge></div><span className={`deadline ${deadline.urgent ? "deadline--urgent" : ""}`}><Clock3 />{deadline.text}</span>{item.state.evidence_collection_degraded ? <span className="evidence-alert"><AlertTriangle />Evidence degraded</span> : <span className="confidence">{formatPercent(item.state.win_probability)} confidence</span>}</li>;
          })}</ol> : <EmptyState title="No cases need attention">New eligible disputes will appear here automatically.</EmptyState>}
        </Panel>
        <Panel title="Recommendation distribution">
          <div className="chart" aria-label="Recommendation distribution chart"><ResponsiveContainer width="100%" height={240}><BarChart data={distribution} margin={{ top: 12, right: 8, bottom: 4, left: -24 }}><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="name" tick={{ fontSize: 11 }} /><YAxis allowDecimals={false} /><Tooltip /><Bar dataKey="value" fill="var(--cg-blue-500)" radius={[5, 5, 0, 0]} /></BarChart></ResponsiveContainer></div>
          <div className="unavailable-note"><strong>Revenue trend unavailable</strong><span>The API provides a current portfolio snapshot but no historical series. No trend has been fabricated.</span></div>
        </Panel>
        <Panel title="Recent activity" className="recent-panel">
          {disputes.slice().sort((a, b) => +new Date(b.updated_at) - +new Date(a.updated_at)).slice(0, 5).map((item) => <a key={item.chargeback_id} href={`#/disputes?case=${encodeURIComponent(item.chargeback_id)}`} className="activity-row"><span className={`activity-dot activity-dot--${recommendationTone(item.state.decision)}`} /><span><strong>{item.chargeback_id}</strong><small>{item.status} · updated {formatDate(item.updated_at)}</small></span><Badge tone={recommendationTone(item.state.decision)}>{item.state.decision ?? "PENDING"}</Badge></a>)}
        </Panel>
      </div>
    </>
  );
}

function PageHeading() { return <header className="page-heading"><div><p className="eyebrow">Command Center</p><h1>Operational clarity, case by case.</h1><p>What requires attention now, and how much recoverable revenue is affected?</p></div><span className="live-indicator"><span />Live portfolio</span></header>; }

function Metric({ label, value, values, helper, icon }: { label: string; value?: string; values?: Record<string, number>; helper?: string; icon: ReactNode }) {
  const entries = Object.entries(values ?? {});
  return <article className="metric-card"><div className="metric-card__top"><span>{label}</span>{icon}</div>{entries.length ? <div className="money-stack">{entries.map(([currency, amount]) => <strong key={currency}>{formatMoney(amount, currency)}</strong>)}</div> : <strong>{value ?? "No data"}</strong>}<small>{entries.length > 1 ? "Currencies kept separate" : helper ?? "Current portfolio"}</small></article>;
}
