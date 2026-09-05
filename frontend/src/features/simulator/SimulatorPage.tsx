import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Play, RefreshCw } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useConnection } from "../../app/ConnectionContext";
import { Badge, Button, EmptyState, ErrorState, Panel, Skeleton } from "../../components/ui";
import { formatDate, formatMoney } from "../../utils/format";

const legalTransitions: Record<string, string[]> = {
  open: ["action_required", "under_review", "closed"],
  action_required: ["under_review", "closed"],
  under_review: ["won", "lost", "closed"],
  won: ["closed"],
  lost: ["closed"],
};

function freshIds() {
  const suffix = globalThis.crypto?.randomUUID?.().replaceAll("-", "").slice(0, 16)
    ?? `${Date.now()}${Math.random().toString(16).slice(2, 8)}`;
  return { payment: `pay_SIM_${suffix}`, order: `order_SIM_${suffix}` };
}

function titleCase(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, character => character.toUpperCase());
}

export function SimulatorPage() {
  const { client, isDemo } = useConnection();
  const qc = useQueryClient();
  const merchants = useQuery({
    queryKey: ["merchants"],
    queryFn: ({ signal }) => client.merchants(signal),
  });
  const simulations = useQuery({
    queryKey: ["simulator"],
    queryFn: ({ signal }) => client.simulatorDisputes(signal),
    retry: false,
  });
  const scenarios = useQuery({
    queryKey: ["simulation-scenarios"],
    queryFn: ({ signal }) => client.simulationScenarios(signal),
    retry: false,
  });
  const razorpayMerchants = merchants.data?.filter(
    merchant => merchant.payment_provider === "razorpay" && merchant.razorpay_account_id,
  ) ?? [];
  const [merchantId, setMerchantId] = useState(isDemo ? "merchant_reviewer_demo" : "");
  const [scenarioId, setScenarioId] = useState("");
  const [ids, setIds] = useState(freshIds);
  const [notice, setNotice] = useState("");

  const groupedScenarios = (() => {
    const groups = new Map<string, NonNullable<typeof scenarios.data>>();
    for (const scenario of scenarios.data ?? []) {
      groups.set(scenario.family, [...(groups.get(scenario.family) ?? []), scenario]);
    }
    return [...groups.entries()];
  })();
  const selectedScenario = scenarios.data?.find(item => item.id === scenarioId)
    ?? scenarios.data?.[0];

  const refreshResults = async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: ["simulator"] }),
      qc.invalidateQueries({ queryKey: ["disputes"] }),
      qc.invalidateQueries({ queryKey: ["provider-events"] }),
    ]);
  };
  const run = useMutation({
    mutationFn: ({ selectedId, selectedMerchant }: { selectedId: string; selectedMerchant: string }) =>
      client.runSimulationScenario(selectedId, selectedMerchant),
    onSuccess: async result => {
      const statuses = result.deliveries.map(item => item.delivery.status_code).join(", ");
      setNotice(`Scenario ${result.scenario_id} ran as ${result.dispute_id}. HTTP delivery status: ${statuses}.`);
      await refreshResults();
    },
  });
  const create = useMutation({
    mutationFn: (body: Record<string, unknown>) => client.createSimulation(body),
    onSuccess: async data => {
      setNotice(`Manual case ${data.dispute_id} seeded its order and returned HTTP ${data.delivery.status_code}.`);
      setIds(freshIds());
      await refreshResults();
    },
  });
  const transition = useMutation({
    mutationFn: ({ disputeId, state }: { disputeId: string; state: string }) =>
      client.transitionSimulation(disputeId, state),
    onSuccess: async data => {
      setNotice(`Delivered ${titleCase(data.state)} for ${data.dispute_id}.`);
      await refreshResults();
    },
  });

  const runSelected = () => {
    if (!merchantId || !selectedScenario) return;
    run.mutate({ selectedId: selectedScenario.id, selectedMerchant: merchantId });
  };
  const submitManual = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    create.mutate({
      merchant_id: form.get("merchant_id"),
      payment_id: form.get("payment_id"),
      order_id: form.get("order_id"),
      payment_amount_paise: Number(form.get("payment_amount_paise")),
      dispute_amount_paise: Number(form.get("dispute_amount_paise")),
      currency: form.get("currency"),
      method: "card",
      card_network: form.get("card_network"),
      network_reason_code: form.get("network_reason_code"),
      razorpay_reason_code: form.get("razorpay_reason_code"),
      respond_within_hours: Number(form.get("respond_within_hours")),
    });
  };

  return <>
    <header className="page-heading">
      <div>
        <p className="eyebrow">Development-only tooling</p>
        <h1>Razorpay scenario simulator</h1>
        <p>Runs synthetic, signed events through the real loopback receiver, order correlation, processor, and graph. It never contacts Razorpay.</p>
      </div>
      <Badge tone="warning">TEST DATA ONLY</Badge>
    </header>

    <div className="sim-grid">
      <Panel title="Run a realistic scenario">
        {scenarios.isLoading ? <Skeleton lines={5} /> : scenarios.error ? <ErrorState error={scenarios.error} retry={() => void scenarios.refetch()} /> : <div className="form-grid">
          <label>Mapped Razorpay merchant
            <select value={merchantId} onChange={event => setMerchantId(event.target.value)} required>
              <option value="">Select merchant</option>
              {razorpayMerchants.map(merchant => <option key={merchant.merchant_id} value={merchant.merchant_id}>{merchant.name}</option>)}
            </select>
          </label>
          <label>Scenario
            <select value={selectedScenario?.id ?? ""} onChange={event => setScenarioId(event.target.value)}>
              {groupedScenarios.map(([family, items]) => <optgroup key={family} label={family}>
                {items.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}
              </optgroup>)}
            </select>
          </label>
          {selectedScenario ? <article className="scenario-preview">
            <Badge tone="info">{selectedScenario.family}</Badge>
            <h3>{selectedScenario.title}</h3>
            <p>{selectedScenario.description}</p>
            <dl>
              <div><dt>Rail</dt><dd>{selectedScenario.payload.method.toUpperCase()}</dd></div>
              <div><dt>Network / reason</dt><dd>{selectedScenario.payload.card_network ?? "none"} / {selectedScenario.payload.network_reason_code ?? "none"}</dd></div>
              <div><dt>Payment amount</dt><dd>{formatMoney(selectedScenario.payload.payment_amount_paise / 100, selectedScenario.payload.currency)}</dd></div>
              <div><dt>Disputed amount</dt><dd>{formatMoney(selectedScenario.payload.dispute_amount_paise / 100, selectedScenario.payload.currency)}</dd></div>
              <div><dt>Deadline</dt><dd>{selectedScenario.payload.respond_within_hours} hours</dd></div>
            </dl>
            <strong>Expected</strong>
            <p>{selectedScenario.expected}</p>
          </article> : null}
          <Button type="button" onClick={runSelected} loading={run.isPending} disabled={!merchantId || !selectedScenario}>
            <Play />Run selected scenario
          </Button>
          {run.error ? <ErrorState error={run.error} /> : null}
        </div>}
      </Panel>

      {!isDemo ? <Panel title="Manual supported-card case">
        <form className="form-grid" onSubmit={submitManual} key={`${ids.payment}:${ids.order}`}>
          <label>Merchant
            <select name="merchant_id" required defaultValue={merchantId} onChange={event => setMerchantId(event.target.value)}>
              <option value="">Select merchant</option>
              {razorpayMerchants.map(merchant => <option key={merchant.merchant_id} value={merchant.merchant_id}>{merchant.name}</option>)}
            </select>
          </label>
          <div className="sim-fields-two">
            <label>Payment ID<input name="payment_id" required defaultValue={ids.payment} /></label>
            <label>Provider order ID<input name="order_id" required defaultValue={ids.order} /></label>
            <label>Payment amount (paise)<input name="payment_amount_paise" type="number" min="1" required defaultValue="150000" /></label>
            <label>Dispute amount (paise)<input name="dispute_amount_paise" type="number" min="1" required defaultValue="120000" /></label>
            <label>Currency<input name="currency" required minLength={3} maxLength={3} defaultValue="INR" /></label>
            <label>Respond within hours<input name="respond_within_hours" type="number" min="-2160" max="2160" required defaultValue="72" /></label>
            <label>Card network<select name="card_network"><option>VISA</option><option>MASTERCARD</option><option>RUPAY</option><option>AMEX</option></select></label>
            <label>Network reason<input name="network_reason_code" required defaultValue="13.1" /></label>
          </div>
          <label>Razorpay provider reason<input name="razorpay_reason_code" required defaultValue="product_not_received" /></label>
          <Button type="submit" loading={create.isPending}><FlaskConical />Create manual test dispute</Button>
          {create.error ? <ErrorState error={create.error} /> : null}
        </form>
      </Panel> : <Panel title="Public demo"><p>Choose any catalog scenario. Manual creation and lifecycle mutations are operator-only. Open Disputes to inspect your results and advisory review.</p></Panel>}
    </div>

    {notice ? <p className="success-box sim-notice" role="status">{notice}</p> : null}

    <Panel
      className="sim-records"
      title="Synthetic dispute records"
      action={<Button type="button" variant="ghost" onClick={() => void simulations.refetch()}><RefreshCw />Refresh</Button>}
    >
      {simulations.isLoading ? <Skeleton lines={5} /> : simulations.error ? <ErrorState error={simulations.error} retry={() => void simulations.refetch()} /> : simulations.data?.length ? <div className="event-list">
        {simulations.data.map(simulation => <article key={simulation.dispute_id}>
          <span>
            <strong>{simulation.dispute_id}</strong>
            <small>{simulation.scenario_id ? `${simulation.scenario_id} · ` : ""}{simulation.order_id} · {simulation.payment_id}</small>
          </span>
          <Badge tone="warning">TEST · {simulation.state}</Badge>
          <b>{formatMoney(simulation.dispute_amount_paise / 100, simulation.currency)}</b>
          <small>Created {formatDate(simulation.created_at)}</small>
          {!isDemo && legalTransitions[simulation.state]?.length ? <div className="button-row sim-transitions">
            {legalTransitions[simulation.state].map(state => <Button
              key={state}
              type="button"
              variant="secondary"
              loading={transition.isPending && transition.variables?.disputeId === simulation.dispute_id}
              onClick={() => transition.mutate({ disputeId: simulation.dispute_id, state })}
            >{titleCase(state)}</Button>)}
          </div> : null}
        </article>)}
      </div> : <EmptyState title="No test disputes">Run a catalog example or create a manual case using a mapped Razorpay test merchant.</EmptyState>}
      {transition.error ? <ErrorState error={transition.error} /> : null}
    </Panel>
  </>;
}
