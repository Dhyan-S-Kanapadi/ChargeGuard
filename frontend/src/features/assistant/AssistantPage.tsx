import { useMutation, useQuery } from "@tanstack/react-query";
import { Bot, Send, Square } from "lucide-react";
import { useRef, useState, type FormEvent } from "react";
import { ApiError } from "../../api/client";
import { useConnection } from "../../app/ConnectionContext";
import { Button, EmptyState, ErrorState, Panel } from "../../components/ui";

const prompts = ["Which supplied cases have the nearest filing deadlines?", "Where is evidence collection degraded?", "Explain the device-risk signals in the supplied cases.", "Summarize the current recommendation mix."];
const modeLabels = { stub: "Simulated response", live_configured: "Live LLM configured", unavailable: "Configuration needed", disabled: "Disabled" };
export function AssistantPage() {
  const { client } = useConnection();
  const [question, setQuestion] = useState("");
  const [caseId, setCaseId] = useState("");
  const controller = useRef<AbortController | null>(null);
  const status = useQuery({ queryKey: ["llm-status"], queryFn: ({ signal }) => client.llmStatus(signal), staleTime: 60000 });
  const mutation = useMutation({ mutationFn: async () => {
    controller.current?.abort();
    controller.current = new AbortController();
    return client.askAssistant(question, caseId || undefined, controller.current.signal);
  } });
  const submit = (e: FormEvent) => { e.preventDefault(); if (question.trim()) mutation.mutate(); };
  const rate = mutation.error instanceof ApiError && mutation.error.status === 429 ? mutation.error.retryAfter : undefined;
  return <>
    <header className="page-heading"><div><p className="eyebrow">Grounded case intelligence</p><h1>Guard AI</h1>
      <p>Answers use workspace case summaries and statistics. Simulator cases are synthetic, not real customer disputes.</p>
    </div></header>
    <div className="assistant-layout">
      <Panel className="assistant-panel">
        <div className="assistant-notice"><Bot />Verify consequential actions in the case record. AI cannot change decisions or file disputes.</div>
        {mutation.data ? <article className="assistant-answer" aria-live="polite"><span><Bot /></span><div>
          <p>{mutation.data.answer}</p><small>Based on {mutation.data.based_on.dispute_count} disputes; stats snapshot {mutation.data.based_on.stats_snapshot ? "included" : "unavailable"}</small>
        </div></article> : mutation.error ? <ErrorState error={mutation.error} retry={() => mutation.mutate()} /> :
          <EmptyState title="Ask about your portfolio">Choose a suggestion or enter an operational question.</EmptyState>}
        {rate ? <p className="warning-box">Rate limited. Try again in approximately {rate} seconds.</p> : null}
        <form className="assistant-form" onSubmit={submit}>
          <label>Optional chargeback context<input value={caseId} onChange={(e) => setCaseId(e.target.value)} placeholder="disp_SIM_... or cb_..." /></label>
          <label>Your question<textarea value={question} onChange={(e) => setQuestion(e.target.value)} required maxLength={4000} /></label>
          <div className="button-row">
            {mutation.isPending ? <Button type="button" variant="secondary" onClick={() => controller.current?.abort()}><Square />Cancel</Button> : null}
            <Button type="submit" loading={mutation.isPending}><Send />Ask Guard AI</Button>
          </div>
        </form>
      </Panel>
      <div>
        <Panel title="LLM features">
          {status.data ? Object.entries(status.data).map(([feature, config]) => <p key={feature}>
            <strong>{feature === "guard_ai" ? "Guard AI chat" : "Advisory decision reviewer"}</strong><br />
            {modeLabels[config.mode]}{config.model ? <><br /><small>{config.model}</small></> : null}
          </p>) : status.error ? <ErrorState error={status.error} retry={() => { void status.refetch(); }} /> : <p>Checking configuration...</p>}
          <p>Configured does not guarantee provider availability. Free-provider limits can temporarily interrupt live AI. See individual reviews in Disputes.</p>
        </Panel>
        <Panel title="Suggested questions"><div className="suggestions">{prompts.map(prompt =>
          <button key={prompt} onClick={() => setQuestion(prompt)}>{prompt}</button>
        )}</div></Panel>
      </div>
    </div>
  </>;
}
