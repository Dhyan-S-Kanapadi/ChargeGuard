import { z } from "zod";
import {
  AssistantResponseSchema,
  DisputeDetailSchema,
  DisputeSummarySchema,
  HealthSchema,
  MerchantSchema,
  OutcomeResponseSchema,
  ProviderEventSchema,
  SimulatorDisputeSchema,
  StatsSchema,
  SummaryResponseSchema,
} from "./schemas";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly retryAfter?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function joinUrl(baseUrl: string, path: string): string {
  const base = new URL(baseUrl, window.location.origin);
  if (!(["http:", "https:"] as string[]).includes(base.protocol)) {
    throw new Error("API URL must use HTTP or HTTPS.");
  }
  base.hash = "";
  base.search = "";
  base.pathname = `${base.pathname.replace(/\/+$/, "")}/`;
  const target = new URL(path.replace(/^\/+/, ""), base);
  if (target.origin !== base.origin) {
    throw new Error("API requests cannot leave the selected origin.");
  }
  return target.toString();
}

function errorMessage(payload: unknown, fallback: string): string {
  if (!payload || typeof payload !== "object" || !("detail" in payload)) return fallback;
  const detail = (payload as { detail: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail.flatMap((item) => {
      if (!item || typeof item !== "object") return [];
      const message = "msg" in item ? String(item.msg) : "Invalid value";
      const location = "loc" in item && Array.isArray(item.loc) ? item.loc.join(".") : "request";
      return `${location}: ${message}`;
    });
    if (messages.length) return messages.join("; ");
  }
  return fallback;
}

type RequestOptions<T> = {
  method?: "GET" | "POST";
  body?: unknown;
  auth?: boolean;
  schema: z.ZodType<T>;
  signal?: AbortSignal;
};

export class ApiClient {
  constructor(
    private readonly baseUrl: string,
    private readonly apiKey: string,
    private readonly timeoutMs = 12_000,
  ) {}

  private async request<T>(path: string, options: RequestOptions<T>): Promise<T> {
    const controller = new AbortController();
    const abort = () => controller.abort();
    if (options.signal?.aborted) abort();
    options.signal?.addEventListener("abort", abort, { once: true });
    const timeout = window.setTimeout(abort, this.timeoutMs);
    try {
      const headers = new Headers({ Accept: "application/json" });
      if (options.auth !== false) headers.set("X-API-Key", this.apiKey);
      if (options.body !== undefined) headers.set("Content-Type", "application/json");
      const response = await fetch(joinUrl(this.baseUrl, path), {
        method: options.method ?? "GET",
        headers,
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
        signal: controller.signal,
      });
      const isJson = response.headers.get("content-type")?.includes("application/json");
      const payload: unknown = isJson ? await response.json() : await response.text();
      if (!response.ok) {
        const retryAfter = Number(response.headers.get("Retry-After"));
        throw new ApiError(
          errorMessage(payload, `Request failed with status ${response.status}.`),
          response.status,
          Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : undefined,
        );
      }
      const parsed = options.schema.safeParse(payload);
      if (!parsed.success) throw new ApiError("The server returned an unexpected response.", 500);
      return parsed.data;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (controller.signal.aborted) throw new ApiError("The request was cancelled or timed out.", 0);
      throw new ApiError("The backend is unavailable.", 0);
    } finally {
      window.clearTimeout(timeout);
      options.signal?.removeEventListener("abort", abort);
    }
  }

  health(signal?: AbortSignal) {
    return this.request("/health", { auth: false, schema: HealthSchema, signal });
  }

  stats(signal?: AbortSignal) {
    return this.request("/stats", { schema: StatsSchema, signal });
  }

  merchants(signal?: AbortSignal) {
    return this.request("/merchants", { schema: MerchantSchema.array(), signal });
  }

  createMerchant(body: Record<string, unknown>) {
    return this.request("/merchants", { method: "POST", body, schema: MerchantSchema });
  }

  disputeSummaries(signal?: AbortSignal) {
    return this.request("/disputes", { schema: DisputeSummarySchema.array(), signal });
  }

  dispute(id: string, signal?: AbortSignal) {
    return this.request(`/disputes/${encodeURIComponent(id)}`, { schema: DisputeDetailSchema, signal });
  }

  async disputes(signal?: AbortSignal) {
    const summaries = await this.disputeSummaries(signal);
    return Promise.all(summaries.map(({ chargeback_id }) => this.dispute(chargeback_id, signal)));
  }

  summary(id: string, signal?: AbortSignal) {
    return this.request(`/disputes/${encodeURIComponent(id)}/summary`, { schema: SummaryResponseSchema, signal });
  }

  recordOutcome(id: string, outcome: "WIN" | "LOSS", reason: string) {
    return this.request(`/disputes/${encodeURIComponent(id)}/outcome`, {
      method: "POST",
      body: { outcome, reason: reason || null },
      schema: OutcomeResponseSchema,
    });
  }

  askAssistant(question: string, chargebackId?: string, signal?: AbortSignal) {
    return this.request("/assistant/query", {
      method: "POST",
      body: { question, chargeback_id: chargebackId || null },
      schema: AssistantResponseSchema,
      signal,
    });
  }

  providerEvents(processingState?: string, signal?: AbortSignal) {
    const query = processingState ? `?processing_state=${encodeURIComponent(processingState)}` : "";
    return this.request(`/internal/razorpay/events${query}`, { schema: ProviderEventSchema.array(), signal });
  }

  retryEvent(id: string) {
    return this.request(`/internal/razorpay/events/${encodeURIComponent(id)}/retry`, {
      method: "POST",
      schema: zObjectStatus,
    });
  }

  processPending(limit: number) {
    return this.request(`/internal/razorpay/process-pending?limit=${limit}`, {
      method: "POST",
      schema: recoveryResultSchema,
    });
  }

  reconcileMerchant(merchantId: string) {
    return this.request("/internal/razorpay/reconcile", {
      method: "POST",
      body: { merchant_id: merchantId, count: 100 },
      schema: reconciliationResultSchema,
    });
  }

  simulatorDisputes(signal?: AbortSignal) {
    return this.request("/dev/razorpay-simulator/disputes", {
      schema: SimulatorDisputeSchema.array(),
      signal,
    });
  }

  createSimulation(body: Record<string, unknown>) {
    return this.request("/dev/razorpay-simulator/disputes", {
      method: "POST",
      body,
      schema: simulatorResultSchema,
    });
  }

  transitionSimulation(id: string, state: string) {
    return this.request(`/dev/razorpay-simulator/disputes/${encodeURIComponent(id)}/transition`, {
      method: "POST",
      body: { state, force: false },
      schema: z.object({ dispute_id: z.string(), state: z.string(), event_id: z.string() }).passthrough(),
    });
  }
}

const zObjectStatus = z.object({ status: z.string(), event_id: z.string() });
const recoveryResultSchema = z.object({
  considered: z.number(),
  scheduled: z.number(),
  skipped: z.number(),
  failed: z.number(),
});
const reconciliationResultSchema = z.object({ count: z.number(), results: z.array(z.record(z.string(), z.unknown())) });
const simulatorResultSchema = z.object({ dispute_id: z.string(), event_id: z.string(), event_name: z.string() }).passthrough();
