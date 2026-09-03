import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, RotateCcw, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { useConnection } from "../../app/ConnectionContext";
import { Badge, Button, ConfirmDialog, EmptyState, ErrorState, Panel, Skeleton } from "../../components/ui";
import { formatDate } from "../../utils/format";

type Action = { kind: "retry" | "pending" | "reconcile"; id?: string } | null;

export function OperationsPage() {
  const { client, selectedMerchantId } = useConnection();
  const queryClient = useQueryClient();
  const [state, setState] = useState("");
  const [action, setAction] = useState<Action>(null);
  const [notice, setNotice] = useState("");
  const events = useQuery({ queryKey: ["provider-events", state], queryFn: ({ signal }) => client.providerEvents(state || undefined, signal) });
  const mutation = useMutation({
    mutationFn: async (value: NonNullable<Action>) => {
      if (value.kind === "retry") return client.retryEvent(value.id!);
      if (value.kind === "pending") return client.processPending(25);
      return client.reconcileMerchant(selectedMerchantId);
    },
    onSuccess: async (_, value) => {
      const label = value.kind === "retry" ? "Retry" : value.kind === "pending" ? "Pending-event recovery" : "Reconciliation";
      setNotice(`${label} was accepted.`); setAction(null); await queryClient.invalidateQueries();
    },
  });
  const confirmation = action?.kind === "retry" ? `Retry event ${action.id}.` : action?.kind === "pending" ? "Queue eligible persisted events for bounded recovery." : `Reconcile merchant ${selectedMerchantId} with Razorpay.`;
  return <>
    <header className="page-heading"><div><p className="eyebrow">Protected workspace</p><h1>Razorpay operations</h1><p>Safe event metadata and explicit recovery actions. Stored webhook bodies are never shown.</p></div><Badge tone="warning">API-key protected</Badge></header>
    <div className="operations-grid">
      <Panel title="Recovery controls"><p>Actions remain subject to backend retry, idempotency, ordering, and merchant-mapping checks.</p><div className="button-stack"><Button variant="secondary" onClick={() => setAction({ kind: "pending" })}><RotateCcw />Process pending (max 25)</Button><Button variant="secondary" disabled={!selectedMerchantId} onClick={() => setAction({ kind: "reconcile" })}><RefreshCw />Reconcile selected merchant</Button></div>{!selectedMerchantId ? <small>Select a merchant workspace before reconciliation.</small> : null}{notice ? <p className="success-box" role="status">{notice}</p> : null}{mutation.error ? <ErrorState error={mutation.error} /> : null}</Panel>
      <Panel title="Provider events" className="events-panel" action={<label>State <select value={state} onChange={(event) => setState(event.target.value)}><option value="">All</option><option>received</option><option>processing</option><option>failed</option><option>unresolved</option><option>processed</option></select></label>}>
        {events.isLoading ? <Skeleton lines={6} /> : events.error ? <ErrorState error={events.error} retry={() => void events.refetch()} /> : events.data?.length ? <div className="event-list">{events.data.map((event) => {
          const tone = event.processing_state === "failed" ? "danger" : event.processing_state === "processed" ? "success" : "warning";
          return <article key={event.event_id}><span><strong>{event.event_type || "Razorpay event"}</strong><small>{event.event_id}</small></span><Badge tone={tone}>{event.processing_state}</Badge><dl><div><dt>Case</dt><dd>{event.chargeback_id || "Unresolved"}</dd></div><div><dt>Attempts</dt><dd>{event.attempt_count}</dd></div><div><dt>Received</dt><dd>{formatDate(event.received_at)}</dd></div></dl>{event.failure_reason ? <p className="failure-reason"><ShieldAlert />{event.failure_reason}</p> : null}{["failed", "unresolved"].includes(event.processing_state) ? <Button variant="secondary" onClick={() => setAction({ kind: "retry", id: event.event_id })}>Retry event</Button> : null}</article>;
        })}</div> : <EmptyState title="No provider events">No safe event metadata matches this state.</EmptyState>}
      </Panel>
    </div>
    <ConfirmDialog open={Boolean(action)} title="Confirm protected operation" confirmLabel="Confirm operation" danger pending={mutation.isPending} onClose={() => setAction(null)} onConfirm={() => { if (action) mutation.mutate(action); }}><p>{confirmation}</p><p>The backend will enforce eligibility and duplicate-event safeguards.</p></ConfirmDialog>
  </>;
}
