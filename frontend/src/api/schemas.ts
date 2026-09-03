import { z } from "zod";

export const HealthSchema = z.object({
  status: z.enum(["ok", "degraded"]),
  model_loaded: z.boolean(),
  stub_mode: z.boolean(),
});

export const MerchantRatioSchema = z.object({
  window_days: z.number(),
  card_network: z.string(),
  dispute_count: z.number(),
  transaction_count: z.number().nullable(),
  current_ratio_pct: z.number().nullable(),
  threshold_pct: z.number().nullable(),
  status: z.enum(["OK", "WARNING", "UNAVAILABLE", "UNCONFIGURED"]),
});

export const MerchantSchema = z.object({
  merchant_id: z.string(),
  name: z.string(),
  vertical: z.string(),
  payment_provider: z.string().nullable().optional(),
  razorpay_account_id: z.string().nullable().optional(),
  shipping_provider: z.string().nullable().optional(),
  support_connector_ref: z.string().nullable().optional(),
  freshdesk_domain: z.string(),
  gmail_user_id: z.string().nullable().optional(),
  average_order_value: z.number(),
  chargeback_history_count: z.number(),
  transaction_volume_30d_by_network: z.record(z.string(), z.number()),
  merchant_dispute_ratio: z.record(z.string(), MerchantRatioSchema),
  store_url: z.string().nullable().optional(),
  storefront_platform: z.enum(["shopify", "woocommerce", "custom", "unknown"]),
  platform_credential_verified: z.boolean(),
  platform_credential_verified_at: z.string().nullable().optional(),
  platform_credential_verification_reason: z.string().nullable().optional(),
});

export const PlatformSuggestionSchema = z.object({
  suggestion: z.enum(["shopify", "woocommerce", "custom", "unknown"]),
});

export const DisputeSummarySchema = z.object({
  chargeback_id: z.string(),
  status: z.string(),
  decision: z.string().nullable(),
  dispute_amount: z.number(),
  currency: z.string(),
  merchant_id: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
});

const ScoreSchema = z.object({
  score: z.number(),
  label: z.string(),
}).passthrough();

const MerchantProfileSchema = z.object({
  merchant_id: z.string(),
  name: z.string(),
  vertical: z.string(),
}).passthrough();

export const DisputeStateSchema = z.object({
  chargeback_id: z.string(),
  order_id: z.string().optional(),
  payment_id: z.string().optional(),
  reason_code: z.string(),
  provider_reason_code: z.string().optional(),
  network_reason_code: z.string().optional(),
  card_network: z.string().nullable(),
  dispute_amount: z.number(),
  currency: z.string(),
  filing_deadline: z.string(),
  merchant_profile: MerchantProfileSchema,
  provider: z.string().optional(),
  provider_dispute_id: z.string().optional(),
  provider_event: z.string().optional(),
  provider_status: z.string().optional(),
  provider_phase: z.string().optional(),
  provider_event_timestamp: z.string().optional(),
  provider_respond_by: z.string().optional(),
  payment_rail: z.string().optional(),
  deadline_overdue: z.boolean().optional(),
  decision: z.enum(["FIGHT", "ACCEPT", "ESCALATE_DEGRADED"]).nullable(),
  decision_reasoning: z.string().nullable(),
  win_probability: z.number().nullable(),
  expected_value: z.number().nullable(),
  evidence_collection_degraded: z.boolean().default(false),
  degraded_reasons: z.array(z.string()).default([]),
  transaction: z.record(z.string(), z.unknown()).nullable(),
  shipping: z.record(z.string(), z.unknown()).nullable(),
  comms: z.record(z.string(), z.unknown()).nullable(),
  device: z.record(z.string(), z.unknown()).nullable(),
  consortium: z.record(z.string(), z.unknown()).nullable(),
  delivery_photo: z.record(z.string(), z.unknown()).nullable(),
  order_timeline: z.record(z.string(), z.unknown()).nullable(),
  third_party_fraud_indicators: ScoreSchema.nullable().optional(),
  identity_continuity: ScoreSchema.nullable().optional(),
  contradiction_summary: z.string().nullable().optional(),
  human_review_summary: z.string().nullable().optional(),
  quality_approved: z.boolean(),
  filing_confirmation: z.string().nullable(),
  filed_at: z.string().nullable(),
  final_outcome: z.enum(["WIN", "LOSS", "PENDING", "ACCEPTED_NO_CONTEST"]).nullable(),
  outcome_reason: z.string().nullable(),
  outcome_recorded_at: z.string().nullable(),
}).passthrough();

export const DisputeDetailSchema = z.object({
  chargeback_id: z.string(),
  status: z.string(),
  state: DisputeStateSchema,
  win_probability: z.number().nullable(),
  expected_value: z.number().nullable(),
  third_party_fraud_indicators: ScoreSchema.nullable().optional(),
  identity_continuity: ScoreSchema.nullable().optional(),
  human_review_summary: z.string().nullable().optional(),
  merchant_dispute_ratio: MerchantRatioSchema.nullable().optional(),
  error: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
});

export const StatsSchema = z.object({
  total_disputes_processed: z.number(),
  decisions: z.object({
    FIGHT: z.number(),
    ACCEPT: z.number(),
    ESCALATE_DEGRADED: z.number(),
  }),
  win_rate: z.number().nullable(),
  average_expected_value: z.number().nullable(),
  evidence_collection_degraded_count: z.number(),
});

export const AssistantResponseSchema = z.object({
  answer: z.string(),
  based_on: z.object({ dispute_count: z.number(), stats_snapshot: z.boolean() }),
});

export const SummaryResponseSchema = z.object({
  chargeback_id: z.string(),
  human_review_summary: z.string(),
});

export const OutcomeResponseSchema = z.object({
  chargeback_id: z.string(),
  final_outcome: z.enum(["WIN", "LOSS"]),
  outcome_reason: z.string(),
  outcome_recorded_at: z.string(),
});

export const ProviderEventSchema = z.object({
  event_id: z.string(),
  event_type: z.string().optional(),
  provider_dispute_id: z.string().nullable().optional(),
  chargeback_id: z.string().nullable().optional(),
  account_id: z.string().nullable().optional(),
  merchant_id: z.string().nullable().optional(),
  processing_state: z.string(),
  received_at: z.string().optional(),
  last_attempt_at: z.string().nullable().optional(),
  attempt_count: z.number().default(0),
  processed_at: z.string().nullable().optional(),
  failure_reason: z.string().nullable().optional(),
}).passthrough();

export const SimulatorDisputeSchema = z.object({
  dispute_id: z.string(),
  merchant_id: z.string(),
  order_id: z.string(),
  payment_id: z.string(),
  currency: z.string(),
  dispute_amount_paise: z.number(),
  method: z.string(),
  state: z.string(),
  created_at: z.string(),
  respond_by: z.string(),
  card_network: z.string().nullable().optional(),
  network_reason_code: z.string().nullable().optional(),
}).passthrough();

export type Health = z.infer<typeof HealthSchema>;
export type Merchant = z.infer<typeof MerchantSchema>;
export type DisputeSummary = z.infer<typeof DisputeSummarySchema>;
export type DisputeState = z.infer<typeof DisputeStateSchema>;
export type DisputeDetail = z.infer<typeof DisputeDetailSchema>;
export type Stats = z.infer<typeof StatsSchema>;
export type ProviderEvent = z.infer<typeof ProviderEventSchema>;
export type SimulatorDispute = z.infer<typeof SimulatorDisputeSchema>;
