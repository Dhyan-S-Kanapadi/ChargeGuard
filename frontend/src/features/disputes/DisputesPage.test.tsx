import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DisputeDetail } from "../../api/schemas";
import {
  ClassificationAssistant,
  DecisionReviewCard,
  classificationSuggestionEligible,
  outcomeEligible,
} from "./DisputesPage";

const mockClient = vi.hoisted(() => ({
  classificationSuggestion: vi.fn(),
  classifyDispute: vi.fn(),
  rejectClassificationSuggestion: vi.fn(),
}));

vi.mock("../../app/ConnectionContext", () => ({
  useConnection: () => ({ client: mockClient }),
}));

const outcome = {
  status: "completed",
  state: {
    decision: "FIGHT",
    quality_approved: true,
    filed_at: "2030-01-01T00:00:00Z",
    filing_confirmation: "filed_stub",
    final_outcome: null,
  },
} as DisputeDetail;

const classification = {
  chargeback_id: "disp_1",
  status: "completed",
  state: {
    provider: "razorpay",
    provider_event: "payment.dispute.created",
    payment_rail: "CARD",
    card_network: "VISA",
    network_reason_code: undefined,
    decision: "ESCALATE_DEGRADED",
    degraded_reasons: ["network_reason_code_unavailable", "network_playbook_unavailable"],
    provider_respond_by: "2099-01-01T00:00:00Z",
    deadline_overdue: false,
  },
} as unknown as DisputeDetail;

const suggestion = {
  suggestion_id: "rcs_1",
  card_network: "VISA",
  recommended_reason_code: "10.4",
  confidence: 0.91,
  rationale: "The provider reason aligns with an unauthorized card-not-present transaction.",
  evidence_fields_used: ["provider_reason_code"],
  status: "pending" as const,
  can_approve: true,
  unavailability_reason: null,
};

function renderAssistant(item: DisputeDetail = classification) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const onChanged = vi.fn().mockResolvedValue(undefined);
  render(<QueryClientProvider client={queryClient}><ClassificationAssistant item={item} onChanged={onChanged} /></QueryClientProvider>);
  return onChanged;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("outcome eligibility", () => {
  it("requires a completed, approved, filed FIGHT case without an outcome", () => {
    expect(outcomeEligible(outcome)).toBe(true);
    expect(outcomeEligible({ ...outcome, state: { ...outcome.state, decision: "ACCEPT" } })).toBe(false);
    expect(outcomeEligible({ ...outcome, state: { ...outcome.state, final_outcome: "WIN" } })).toBe(false);
  });
});

describe("AI decision review", () => {
  const reviewState = {
    ...outcome.state,
    llm_decision_review: {
      status: "completed" as const,
      recommendation: "FIGHT" as const,
      confidence: 0.86,
      summary: "Authenticated payment and delivery evidence support contesting.",
      supporting_factors: ["OTP authentication is present"],
      opposing_factors: ["No qualifying history"],
      missing_evidence: [],
      risk_flags: [],
      agreement_with_engine: true,
      model: "open-weight-demo",
      generated_at: "2030-01-01T00:00:00Z",
      error_code: null,
    },
  } as unknown as DisputeDetail["state"];

  it("renders a completed advisory review", () => {
    render(<DecisionReviewCard state={reviewState} />);

    expect(screen.getByText("AI Decision Review")).toBeInTheDocument();
    expect(screen.getByText(/Advisory analysis only/)).toBeInTheDocument();
    expect(screen.getByText("Agreement")).toBeInTheDocument();
    expect(screen.getByText("86.0%")).toBeInTheDocument();
    expect(screen.getByText("OTP authentication is present")).toBeInTheDocument();
    expect(screen.getByText(/open-weight-demo/)).toBeInTheDocument();
  });

  it("prominently renders disagreement without changing the final decision", () => {
    const state = {
      ...reviewState,
      decision: "ACCEPT" as const,
      llm_decision_review: {
        ...reviewState.llm_decision_review!,
        agreement_with_engine: false,
      },
    };
    render(<DecisionReviewCard state={state} />);

    expect(screen.getByText("Disagreement")).toBeInTheDocument();
    expect(screen.getByText("ACCEPT")).toBeInTheDocument();
    expect(screen.getByText("FIGHT")).toBeInTheDocument();
  });

  it("renders the safe unavailable state", () => {
    const state = {
      ...reviewState,
      llm_decision_review: {
        ...reviewState.llm_decision_review!,
        status: "unavailable" as const,
        recommendation: null,
        confidence: null,
        agreement_with_engine: null,
      },
    };
    render(<DecisionReviewCard state={state} />);

    expect(screen.getByText("AI review unavailable. ChargeGuard’s deterministic decision was preserved.")).toBeInTheDocument();
  });
});

describe("reason classification assistance", () => {
  it("is shown only for safely eligible classification-blocked cases", () => {
    expect(classificationSuggestionEligible(classification)).toBe(true);
    expect(classificationSuggestionEligible({ ...classification, state: { ...classification.state, card_network: null } })).toBe(false);
    expect(classificationSuggestionEligible({ ...classification, state: { ...classification.state, payment_rail: "UPI" } })).toBe(false);
    expect(classificationSuggestionEligible({ ...classification, state: { ...classification.state, degraded_reasons: [...classification.state.degraded_reasons, "enrichment_failed"] } })).toBe(false);
  });

  it("renders loading, recommendation, and human approval states", async () => {
    let resolveSuggestion: (value: typeof suggestion) => void = () => undefined;
    mockClient.classificationSuggestion.mockReturnValue(new Promise((resolve) => { resolveSuggestion = resolve; }));
    mockClient.classifyDispute.mockResolvedValue({ status: "scheduled" });
    renderAssistant();
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Operator ID"), "operator-1");
    const generate = screen.getByRole("button", { name: /Generate AI suggestion/ });
    await user.click(generate);
    expect(generate).toBeDisabled();
    resolveSuggestion(suggestion);

    expect(await screen.findByText("10.4")).toBeInTheDocument();
    expect(screen.getByText(/AI recommendation requiring human approval/)).toBeInTheDocument();
    expect(screen.getByText(/Model confidence \(uncalibrated\)/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Approve and run agents" }));
    await waitFor(() => expect(mockClient.classifyDispute).toHaveBeenCalledWith(
      "disp_1", "VISA", "10.4", "operator-1", "rcs_1",
    ));
  });

  it("renders safe failure and low-confidence states while keeping manual classification", async () => {
    mockClient.classificationSuggestion.mockRejectedValue(new Error("AI classification unavailable"));
    renderAssistant();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Operator ID"), "operator-1");
    await user.click(screen.getByRole("button", { name: /Generate AI suggestion/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("AI classification unavailable");
    expect(screen.getByText("Manual classification")).toBeInTheDocument();

    const unavailable = {
      ...classification,
      state: {
        ...classification.state,
        classification_suggestion: {
          ...suggestion,
          confidence: 0.4,
          status: "unavailable" as const,
          can_approve: false,
          unavailability_reason: "confidence_below_threshold",
        },
      },
    } as DisputeDetail;
    renderAssistant(unavailable);
    expect(screen.getByText(/confidence below threshold/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve and run agents" })).not.toBeInTheDocument();
  });
});
