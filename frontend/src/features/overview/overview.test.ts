import { describe, expect, it } from "vitest";
import type { DisputeDetail } from "../../api/schemas";
import { rankActionRadar, sumByCurrency } from "./overview";

function dispute(id: string, currency: string, amount: number, deadline: string, degraded = false): DisputeDetail {
  return { chargeback_id: id, status: "completed", state: { chargeback_id: id, reason_code: "test", card_network: "VISA", dispute_amount: amount, currency, filing_deadline: deadline, merchant_profile: { merchant_id: "m1", name: "Merchant", vertical: "ecommerce" }, decision: "FIGHT", decision_reasoning: null, win_probability: .7, expected_value: amount / 2, evidence_collection_degraded: degraded, degraded_reasons: [], transaction: null, shipping: null, comms: null, device: null, consortium: null, delivery_photo: null, order_timeline: null, quality_approved: false, filing_confirmation: null, filed_at: null, final_outcome: null, outcome_reason: null, outcome_recorded_at: null }, win_probability: .7, expected_value: amount / 2, error: null, created_at: deadline, updated_at: deadline };
}

describe("overview calculations", () => {
  const later = "2030-02-01T00:00:00Z"; const sooner = "2030-01-01T00:00:00Z";
  it("never combines currencies", () => expect(sumByCurrency([dispute("a", "INR", 100, later), dispute("b", "USD", 2, later)], item => item.state.dispute_amount)).toEqual({ INR: 100, USD: 2 }));
  it("ranks degraded evidence before deadline and expected value", () => expect(rankActionRadar([dispute("later", "INR", 1000, later), dispute("urgent", "INR", 100, sooner), dispute("degraded", "INR", 50, later, true)]).map(item => item.chargeback_id)).toEqual(["degraded", "urgent", "later"]));
});
