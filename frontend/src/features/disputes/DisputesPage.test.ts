import { describe, expect, it } from "vitest";
import type { DisputeDetail } from "../../api/schemas";
import { outcomeEligible } from "./DisputesPage";

const eligible = { status: "completed", state: { decision: "FIGHT", quality_approved: true, filed_at: "2030-01-01T00:00:00Z", filing_confirmation: "filed_stub", final_outcome: null } } as DisputeDetail;
describe("outcome eligibility", () => {
  it("requires a completed, approved, filed FIGHT case without an outcome", () => { expect(outcomeEligible(eligible)).toBe(true); expect(outcomeEligible({ ...eligible, state: { ...eligible.state, decision: "ACCEPT" } })).toBe(false); expect(outcomeEligible({ ...eligible, state: { ...eligible.state, final_outcome: "WIN" } })).toBe(false); });
});
