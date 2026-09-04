"""Optional advisory review that cannot alter ChargeGuard's decision."""

import logging
from datetime import datetime, timezone

from core.state import ChargebackState, LLMDecisionReview
from integrations.decision_review import (
    DecisionReviewClient,
    DecisionReviewConfigError,
    DecisionReviewRequestError,
    decision_review_enabled,
    decision_review_facts,
    decision_review_uses_stubs,
    stub_decision_review,
)


logger = logging.getLogger(__name__)
_AUTHORITATIVE_FIELDS = (
    "decision",
    "win_probability",
    "expected_value",
    "quality_approved",
    "filing_confirmation",
    "filed_at",
    "final_outcome",
    "outcome_reason",
    "outcome_recorded_at",
)


def _empty_review(status: str, error_code: str | None = None) -> LLMDecisionReview:
    return {
        "status": status,
        "recommendation": None,
        "confidence": None,
        "summary": None,
        "supporting_factors": [],
        "opposing_factors": [],
        "missing_evidence": [],
        "risk_flags": [],
        "agreement_with_engine": None,
        "model": None,
        "generated_at": None,
        "error_code": error_code,
    }


def decision_review_agent(state: ChargebackState) -> ChargebackState:
    """Store an advisory model review while preserving authoritative fields."""
    authoritative = {key: state.get(key) for key in _AUTHORITATIVE_FIELDS}
    try:
        if not decision_review_enabled():
            state["llm_decision_review"] = _empty_review("disabled")
            return state
        if state.get("decision") not in {"FIGHT", "ACCEPT", "ESCALATE_DEGRADED"}:
            state["llm_decision_review"] = _empty_review(
                "unavailable", "deterministic_decision_unavailable"
            )
            return state

        facts = decision_review_facts(state)
        if decision_review_uses_stubs():
            result = stub_decision_review(facts)
            model = "decision-review-stub-v1"
        else:
            client = DecisionReviewClient.from_env()
            result = client.review(facts)
            model = client.model
        state["llm_decision_review"] = {
            "status": "completed",
            "recommendation": result.recommendation,
            "confidence": result.confidence,
            "summary": result.summary,
            "supporting_factors": result.supporting_factors,
            "opposing_factors": result.opposing_factors,
            "missing_evidence": result.missing_evidence,
            "risk_flags": result.risk_flags,
            "agreement_with_engine": result.recommendation == authoritative["decision"],
            "model": model,
            "generated_at": datetime.now(timezone.utc),
            "error_code": None,
        }
    except DecisionReviewConfigError:
        state["llm_decision_review"] = _empty_review("unavailable", "configuration_unavailable")
    except DecisionReviewRequestError as exc:
        logger.warning(
            "AI decision review unavailable",
            extra={"chargeback_id": state["chargeback_id"], "error_code": exc.code},
        )
        state["llm_decision_review"] = _empty_review("unavailable", exc.code)
    except Exception:
        logger.error(
            "AI decision review failed",
            extra={"chargeback_id": state["chargeback_id"], "error_code": "internal_error"},
        )
        state["llm_decision_review"] = _empty_review("unavailable", "internal_error")
    finally:
        for key, value in authoritative.items():
            state[key] = value
    return state
