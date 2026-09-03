import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  FileText,
  RefreshCw,
  Sparkles,
  X,
} from "lucide-react";
import { useMemo, useState } from "react";
import type { DisputeDetail } from "../../api/schemas";
import { useConnection } from "../../app/ConnectionContext";
import {
  Badge,
  Button,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  Panel,
  Skeleton,
} from "../../components/ui";
import {
  deadlineLabel,
  formatDate,
  formatMoney,
  formatPercent,
  recommendationTone,
} from "../../utils/format";

const PAGE_SIZE = 10;

function caseFromHash() {
  return new URLSearchParams(location.hash.split("?")[1] ?? "").get("case") ?? "";
}

export function outcomeEligible(item: DisputeDetail) {
  const s = item.state;
  return item.status === "completed"
    && s.decision === "FIGHT"
    && s.quality_approved
    && Boolean(s.filed_at)
    && s.filing_confirmation?.startsWith("filed") === true
    && !s.final_outcome;
}

export function classificationSuggestionEligible(item: DisputeDetail) {
  const s = item.state;
  const reasons = new Set(s.degraded_reasons);
  const allowed = new Set(["network_reason_code_unavailable", "network_playbook_unavailable"]);
  return item.status === "completed"
    && s.provider === "razorpay"
    && s.provider_event === "payment.dispute.created"
    && s.payment_rail === "CARD"
    && Boolean(s.card_network)
    && !s.network_reason_code
    && s.decision === "ESCALATE_DEGRADED"
    && reasons.has("network_reason_code_unavailable")
    && [...reasons].every((reason) => allowed.has(reason))
    && Boolean(s.provider_respond_by)
    && !s.deadline_overdue
    && +new Date(s.provider_respond_by ?? 0) > Date.now();
}

export function DisputesPage() {
  const { client, selectedMerchantId } = useConnection();
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["disputes", "details"],
    queryFn: ({ signal }) => client.disputes(signal),
  });
  const [search, setSearch] = useState(
    () => new URLSearchParams(location.hash.split("?")[1] ?? "").get("q") ?? "",
  );
  const [decision, setDecision] = useState("");
  const [status, setStatus] = useState("");
  const [currency, setCurrency] = useState("");
  const [sort, setSort] = useState("deadline");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState(caseFromHash);
  const rows = useMemo(
    () => (query.data ?? [])
      .filter((item) => {
        const haystack = `${item.chargeback_id} ${item.state.merchant_profile.name} ${item.state.reason_code} ${item.state.provider_reason_code ?? ""}`.toLowerCase();
        return (!selectedMerchantId || item.state.merchant_profile.merchant_id === selectedMerchantId)
          && (!search || haystack.includes(search.toLowerCase()))
          && (!decision || item.state.decision === decision)
          && (!status || item.status === status)
          && (!currency || item.state.currency === currency);
      })
      .sort((a, b) => {
        if (sort === "amount") return b.state.dispute_amount - a.state.dispute_amount;
        if (sort === "updated") return +new Date(b.updated_at) - +new Date(a.updated_at);
        return +new Date(a.state.filing_deadline) - +new Date(b.state.filing_deadline);
      }),
    [query.data, search, decision, status, currency, selectedMerchantId, sort],
  );
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const currentPage = Math.min(page, pages);
  const visible = rows.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  if (query.isLoading) return <><Heading /><Skeleton lines={8} /></>;
  if (query.error) {
    return <><Heading /><ErrorState error={query.error} retry={() => void query.refetch()} /></>;
  }

  return <>
    <Heading />
    <Panel className="table-panel">
      <div className="filters">
        <label>Search<input value={search} onChange={(event) => { setSearch(event.target.value); setPage(1); }} placeholder="Case, merchant, reason" /></label>
        <label>Recommendation<select value={decision} onChange={(event) => setDecision(event.target.value)}><option value="">All</option><option>FIGHT</option><option>ACCEPT</option><option>ESCALATE_DEGRADED</option></select></label>
        <label>Processing<select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">All</option>{[...new Set((query.data ?? []).map((item) => item.status))].map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>Currency<select value={currency} onChange={(event) => setCurrency(event.target.value)}><option value="">All</option>{[...new Set((query.data ?? []).map((item) => item.state.currency))].map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>Sort<select value={sort} onChange={(event) => setSort(event.target.value)}><option value="deadline">Deadline</option><option value="amount">Amount</option><option value="updated">Last updated</option></select></label>
        <Button variant="secondary" onClick={() => void query.refetch()} loading={query.isFetching}><RefreshCw />Refresh</Button>
      </div>
      <p className="filter-summary">{rows.length} of {query.data?.length ?? 0} cases shown{[search, decision, status, currency].filter(Boolean).length ? ` · ${[search, decision, status, currency].filter(Boolean).length} active filters` : ""}</p>
      {visible.length ? <>
        <div className="table-wrap"><table><thead><tr><th>Case</th><th>Merchant</th><th>Amount</th><th>Processing</th><th>Recommendation</th><th>Evidence</th><th>Confidence</th><th>Deadline</th><th>Updated</th></tr></thead><tbody>{visible.map((item) => <tr key={item.chargeback_id} onClick={() => setSelected(item.chargeback_id)}><td><button className="link-button">{item.chargeback_id}</button><small>{item.state.provider_reason_code || item.state.reason_code}</small></td><td>{item.state.merchant_profile.name}</td><td>{formatMoney(item.state.dispute_amount, item.state.currency)}</td><td><Badge>{item.status}</Badge></td><td><Badge tone={recommendationTone(item.state.decision)}>{item.state.decision ?? "PENDING"}</Badge></td><td>{item.state.evidence_collection_degraded ? <span className="evidence-alert"><AlertTriangle />Degraded</span> : "Ready"}</td><td>{formatPercent(item.state.win_probability)}</td><td>{deadlineLabel(item.state.filing_deadline).text}</td><td>{formatDate(item.updated_at)}</td></tr>)}</tbody></table></div>
        <div className="case-cards">{visible.map((item) => <button key={item.chargeback_id} onClick={() => setSelected(item.chargeback_id)}><span><strong>{item.chargeback_id}</strong><Badge tone={recommendationTone(item.state.decision)}>{item.state.decision ?? "PENDING"}</Badge></span><b>{formatMoney(item.state.dispute_amount, item.state.currency)}</b><small>{item.state.merchant_profile.name} · {deadlineLabel(item.state.filing_deadline).text}</small></button>)}</div>
        <div className="pagination"><Button variant="ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}><ChevronLeft />Previous</Button><span>Page {currentPage} of {pages}</span><Button variant="ghost" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next<ChevronRight /></Button></div>
      </> : <EmptyState title={query.data?.length ? "No cases match" : "No disputes yet"}>{query.data?.length ? "Clear or adjust the active filters." : "Processed disputes will appear here."}</EmptyState>}
    </Panel>
    {selected ? <DetailDrawer id={selected} close={() => setSelected("")} invalidate={() => queryClient.invalidateQueries()} /> : null}
  </>;
}

function Heading() {
  return <header className="page-heading"><div><p className="eyebrow">Case workspace</p><h1>Disputes</h1><p>Recommendations, processing state, and final network outcomes remain distinct.</p></div></header>;
}

function DetailDrawer({ id, close, invalidate }: { id: string; close: () => void; invalidate: () => Promise<unknown> }) {
  const { client } = useConnection();
  const detail = useQuery({ queryKey: ["dispute", id], queryFn: ({ signal }) => client.dispute(id, signal) });
  const summary = useQuery({ queryKey: ["summary", id], queryFn: ({ signal }) => client.summary(id, signal), enabled: false, retry: false });
  const [confirm, setConfirm] = useState<"WIN" | "LOSS" | "">("");
  const [reason, setReason] = useState("");
  const [success, setSuccess] = useState("");
  const outcome = useMutation({
    mutationFn: (value: "WIN" | "LOSS") => client.recordOutcome(id, value, reason),
    onSuccess: async () => {
      setSuccess(`Network outcome recorded for ${id}.`);
      setConfirm("");
      await Promise.all([detail.refetch(), invalidate()]);
    },
  });

  return <>
    <button className="drawer-scrim" onClick={close} aria-label="Close case" />
    <aside className="detail-drawer" aria-label={`Case ${id}`}>
      <header><div><p className="eyebrow">Case detail</p><h2>{id}</h2></div><button className="icon-button" onClick={close} aria-label="Close case"><X /></button></header>
      {detail.isLoading ? <Skeleton lines={10} /> : detail.error ? <ErrorState error={detail.error} retry={() => void detail.refetch()} /> : detail.data ? <div className="detail-body">
        <section className="detail-hero"><span><small>Claim amount</small><strong>{formatMoney(detail.data.state.dispute_amount, detail.data.state.currency)}</strong></span><Badge tone={recommendationTone(detail.data.state.decision)}>{detail.data.state.decision ?? "PENDING"} recommendation</Badge></section>
        {detail.data.state.evidence_collection_degraded ? <div className="warning-box"><AlertTriangle />Evidence collection is degraded: {detail.data.state.degraded_reasons.join(", ") || "reason unavailable"}</div> : null}
        {classificationSuggestionEligible(detail.data) ? <ClassificationAssistant item={detail.data} onChanged={async () => { await Promise.all([detail.refetch(), invalidate()]); }} /> : null}
        <dl className="detail-grid"><Field label="Processing status" value={detail.data.status} /><Field label="Network outcome" value={detail.data.state.final_outcome ?? "Not adjudicated"} /><Field label="Confidence" value={formatPercent(detail.data.state.win_probability)} /><Field label="Expected value" value={detail.data.state.expected_value == null ? "Unavailable" : formatMoney(detail.data.state.expected_value, detail.data.state.currency)} /><Field label="Filing deadline" value={formatDate(detail.data.state.filing_deadline)} /><Field label="Filed at" value={formatDate(detail.data.state.filed_at)} /><Field label="Merchant" value={detail.data.state.merchant_profile.name} /><Field label="Reason" value={detail.data.state.provider_reason_code || detail.data.state.reason_code} /><Field label="Payment ID" value={detail.data.state.payment_id ?? "Unavailable"} /><Field label="Order ID" value={detail.data.state.order_id ?? "Unavailable"} /></dl>
        <Section title="Decision reasoning" value={detail.data.state.decision_reasoning} />
        <Section title="Contradiction summary" value={detail.data.state.contradiction_summary} />
        <Panel title="Evidence checklist">{["transaction", "shipping", "comms", "device", "consortium", "delivery_photo", "order_timeline"].map((key) => <span className="check-item" key={key}>{detail.data!.state[key as keyof typeof detail.data.state] ? "✓" : "—"} {key.replaceAll("_", " ")}</span>)}</Panel>
        <Panel title="Human review summary" action={<Button variant="secondary" onClick={() => void summary.refetch()} loading={summary.isFetching}><FileText />Generate</Button>}>{summary.error ? <ErrorState error={summary.error} retry={() => void summary.refetch()} /> : <p>{summary.data?.human_review_summary ?? detail.data.state.human_review_summary ?? "Generate a grounded summary when needed."}</p>}</Panel>
        {outcomeEligible(detail.data) ? <Panel title="Record final card-network outcome"><p>This is separate from ChargeGuard’s {detail.data.state.decision} recommendation.</p><div className="button-row"><Button onClick={() => setConfirm("WIN")}>Record WIN</Button><Button variant="danger" onClick={() => setConfirm("LOSS")}>Record LOSS</Button></div></Panel> : null}
        {success ? <p className="success-box" role="status">{success}</p> : null}
        {outcome.error ? <ErrorState error={outcome.error} /> : null}
      </div> : null}
    </aside>
    <ConfirmDialog open={Boolean(confirm)} title="Confirm final network outcome" confirmLabel={`Record ${confirm}`} danger={confirm === "LOSS"} pending={outcome.isPending} onClose={() => setConfirm("")} onConfirm={() => outcome.mutate(confirm as "WIN" | "LOSS")}><p>Case <strong>{id}</strong> will be recorded as <strong>{confirm}</strong>. This cannot be replaced by a conflicting outcome.</p><label>Reason (optional)<textarea value={reason} onChange={(event) => setReason(event.target.value)} maxLength={2000} /></label></ConfirmDialog>
  </>;
}

export function ClassificationAssistant({ item, onChanged }: { item: DisputeDetail; onChanged: () => Promise<void> }) {
  const { client } = useConnection();
  const [actorId, setActorId] = useState("");
  const [manualCode, setManualCode] = useState("");
  const generate = useMutation({
    mutationFn: () => client.classificationSuggestion(item.chargeback_id, actorId),
  });
  const suggestion = generate.data ?? item.state.classification_suggestion;
  const approve = useMutation({
    mutationFn: () => client.classifyDispute(
      item.chargeback_id,
      suggestion!.card_network,
      suggestion!.recommended_reason_code!,
      actorId,
      suggestion!.suggestion_id,
    ),
    onSuccess: onChanged,
  });
  const reject = useMutation({
    mutationFn: () => client.rejectClassificationSuggestion(
      item.chargeback_id,
      suggestion!.suggestion_id,
      actorId,
    ),
    onSuccess: onChanged,
  });
  const manual = useMutation({
    mutationFn: () => client.classifyDispute(
      item.chargeback_id,
      item.state.card_network!,
      manualCode,
      actorId,
    ),
    onSuccess: onChanged,
  });
  const canApprove = suggestion?.status === "pending"
    && Boolean(suggestion.recommended_reason_code)
    && suggestion.can_approve !== false;

  return <Panel title="Reason classification assistance" className="classification-panel">
    <p><strong>AI recommendation only.</strong> A human must approve it before any evidence agents run.</p>
    <label>Operator ID<input value={actorId} onChange={(event) => setActorId(event.target.value)} maxLength={200} required /></label>
    {!suggestion || suggestion.status === "rejected" ? <Button onClick={() => generate.mutate()} loading={generate.isPending} disabled={!actorId.trim()}><Sparkles />Generate AI suggestion</Button> : null}
    {generate.error ? <ErrorState error={generate.error} retry={() => generate.mutate()} /> : null}
    {suggestion && suggestion.status !== "rejected" ? <div className={canApprove ? "form-notice" : "warning-box"} role="status">
      <p><strong>AI recommendation requiring human approval</strong></p>
      <p>Network reason code: <strong>{suggestion.recommended_reason_code ?? "No recommendation"}</strong></p>
      <p>Model confidence (uncalibrated): <strong>{formatPercent(suggestion.confidence)}</strong></p>
      <p>{suggestion.rationale}</p>
      {suggestion.unavailability_reason ? <p>Unavailable: {suggestion.unavailability_reason.replaceAll("_", " ")}</p> : null}
      {canApprove ? <div className="button-row"><Button onClick={() => approve.mutate()} loading={approve.isPending} disabled={!actorId.trim()}>Approve and run agents</Button><Button variant="secondary" onClick={() => reject.mutate()} loading={reject.isPending} disabled={!actorId.trim()}>Reject suggestion</Button></div> : null}
    </div> : null}
    {approve.error ? <ErrorState error={approve.error} /> : null}
    {reject.error ? <ErrorState error={reject.error} /> : null}
    <details><summary>Manual classification</summary><form className="form-grid" onSubmit={(event) => { event.preventDefault(); manual.mutate(); }}><p>Use the verified {item.state.card_network} network and an existing supported reason code.</p><label>Network reason code<input value={manualCode} onChange={(event) => setManualCode(event.target.value)} maxLength={20} required /></label><Button type="submit" variant="secondary" loading={manual.isPending} disabled={!actorId.trim() || !manualCode.trim()}>Apply manual classification and run agents</Button>{manual.error ? <ErrorState error={manual.error} /> : null}</form></details>
  </Panel>;
}

function Field({ label, value }: { label: string; value: string }) {
  return <div><dt>{label}</dt><dd>{value}</dd></div>;
}

function Section({ title, value }: { title: string; value?: string | null }) {
  return <section><h3>{title}</h3><p>{value || "Unavailable"}</p></section>;
}
