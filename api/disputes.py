from copy import deepcopy
from datetime import datetime, timezone
import os
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query

from analytics.merchant_stats import merchant_dispute_ratio
from api.auth import require_api_key
from api.schemas import (
    CaseSummaryResponse,
    ClassificationSuggestionRejectRequest,
    ClassificationSuggestionRequest,
    ClassificationSuggestionResponse,
    DisputeDetail,
    DisputeClassificationRequest,
    DisputeClassificationResponse,
    DisputeSummary,
    OutcomeResponse,
    OutcomeUpdate,
)
from api.store import store
from api.webhooks import run_chargeback_graph
from api.razorpay_service import _playbook_available, reason_classification_candidates
from core.outcomes import (
    OutcomeConflictError,
    OutcomeNotEligibleError,
    record_adjudicated_outcome,
)
from integrations.case_summary import generate_case_summary
from integrations.reason_classification import (
    PROMPT_SCHEMA_VERSION,
    ReasonClassificationConfigError,
    ReasonClassificationRequestError,
    generate_reason_recommendation,
    reason_classification_enabled,
    reason_classification_min_confidence,
)


router = APIRouter(
    prefix="/disputes",
    tags=["disputes"],
    dependencies=[Depends(require_api_key)],
)


_EVIDENCE_KEYS = (
    "transaction",
    "shipping",
    "comms",
    "device",
    "consortium",
    "delivery_photo",
    "order_timeline",
)
_TRANSACTION_PII_KEYS = ("customer_email", "ip_address", "device_id")
_MERCHANT_SECRET_KEYS = (
    "shopify_admin_api_token",
    "woocommerce_api_key",
    "woocommerce_api_secret",
)
_LLM_REVIEW_SAFE_KEYS = (
    "status",
    "recommendation",
    "confidence",
    "summary",
    "supporting_factors",
    "opposing_factors",
    "missing_evidence",
    "risk_flags",
    "agreement_with_engine",
    "model",
    "generated_at",
    "error_code",
)


def _redact_state(state: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(state)
    for key in _EVIDENCE_KEYS:
        evidence = redacted.get(key)
        if isinstance(evidence, dict):
            evidence.pop("raw", None)

    transaction = redacted.get("transaction")
    if isinstance(transaction, dict):
        for key in _TRANSACTION_PII_KEYS:
            transaction.pop(key, None)

    merchant = redacted.get("merchant_profile")
    if isinstance(merchant, dict):
        for key in _MERCHANT_SECRET_KEYS:
            merchant.pop(key, None)
    redacted.pop("disputed_order_ip", None)
    redacted.pop("disputed_order_user_agent", None)

    review = redacted.get("llm_decision_review")
    if isinstance(review, dict):
        redacted["llm_decision_review"] = {
            key: review[key] for key in _LLM_REVIEW_SAFE_KEYS if key in review
        }
    elif review is not None:
        redacted.pop("llm_decision_review", None)

    return redacted


def _authorize_raw_access(include_raw: bool, internal_token: str | None) -> None:
    if not include_raw:
        return

    expected_token = os.getenv("INTERNAL_API_TOKEN")
    if not expected_token or internal_token != expected_token:
        raise HTTPException(status_code=403, detail="Internal token required.")


@router.get("", response_model=list[DisputeSummary])
def list_disputes() -> list[DisputeSummary]:
    summaries: list[DisputeSummary] = []
    for record in store.list_disputes():
        state = _redact_state(record["state"])
        summaries.append(
            DisputeSummary(
                chargeback_id=record["chargeback_id"],
                status=record["status"],
                decision=state.get("decision"),
                dispute_amount=state["dispute_amount"],
                currency=state["currency"],
                merchant_id=state["merchant_profile"]["merchant_id"],
                created_at=record["created_at"],
                updated_at=record["updated_at"],
            )
        )
    return summaries


@router.get("/{chargeback_id}", response_model=DisputeDetail)
def get_dispute(
    chargeback_id: str,
    include_raw: bool = Query(default=False),
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> DisputeDetail:
    _authorize_raw_access(include_raw, x_internal_token)
    record = store.get_dispute(chargeback_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Dispute not found.")
    if not include_raw:
        record["state"] = _redact_state(record["state"])
    state = record["state"]
    merchant = state["merchant_profile"]
    return DisputeDetail(
        **record,
        win_probability=state.get("win_probability"),
        expected_value=state.get("expected_value"),
        third_party_fraud_indicators=state.get("third_party_fraud_indicators"),
        identity_continuity=state.get("identity_continuity"),
        human_review_summary=state.get("human_review_summary"),
        merchant_dispute_ratio=(
            merchant_dispute_ratio(
                merchant,
                store.list_disputes(),
                state["card_network"],
            )
            if state.get("card_network")
            else None
        ),
    )


@router.get("/{chargeback_id}/summary", response_model=CaseSummaryResponse)
def get_dispute_summary(chargeback_id: str) -> CaseSummaryResponse:
    record = store.get_dispute(chargeback_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Dispute not found.")
    try:
        summary = generate_case_summary(record["state"])
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Case summary generation is unavailable.") from exc
    return CaseSummaryResponse(chargeback_id=chargeback_id, human_review_summary=summary)


_CLASSIFICATION_REASONS = frozenset(
    {"network_reason_code_unavailable", "network_playbook_unavailable"}
)


def _suggestion_ineligibility(record: dict[str, Any]) -> str | None:
    state = record["state"]
    if record.get("status") != "completed":
        return "dispute_processing_not_complete"
    if state.get("provider") != "razorpay":
        return "unsupported_provider"
    if state.get("provider_event") != "payment.dispute.created":
        return "unsupported_provider_event"
    if state.get("payment_rail") != "CARD":
        return "non_card_payment"
    if not state.get("card_network"):
        return "verified_card_network_unavailable"
    if state.get("network_reason_code"):
        return "network_reason_code_already_available"
    if (
        state.get("decision") != "ESCALATE_DEGRADED"
        or not state.get("requires_human_review")
    ):
        return "classification_manual_review_not_active"
    reasons = set(state.get("degraded_reasons", []))
    if "network_reason_code_unavailable" not in reasons:
        return "network_reason_code_review_not_active"
    if reasons - _CLASSIFICATION_REASONS:
        return "unrelated_manual_review_blocker"
    deadline = state.get("provider_respond_by")
    if not isinstance(deadline, datetime):
        return "filing_deadline_unavailable"
    normalized_deadline = deadline.replace(tzinfo=deadline.tzinfo or timezone.utc)
    if state.get("deadline_overdue") or normalized_deadline <= datetime.now(timezone.utc):
        return "filing_deadline_overdue"
    if state.get("classification_resume_scheduled"):
        return "classification_already_scheduled"
    return None


def _suggestion_response(
    suggestion: dict[str, Any],
    *,
    minimum_confidence: float,
) -> ClassificationSuggestionResponse:
    can_approve = bool(
        suggestion.get("status") == "pending"
        and suggestion.get("recommended_reason_code")
        and suggestion.get("confidence", 0) >= minimum_confidence
        and _playbook_available(
            suggestion.get("card_network"),
            suggestion.get("recommended_reason_code"),
        )
    )
    return ClassificationSuggestionResponse(
        suggestion_id=suggestion["suggestion_id"],
        card_network=suggestion["card_network"],
        recommended_reason_code=suggestion.get("recommended_reason_code"),
        confidence=suggestion["confidence"],
        rationale=suggestion["rationale"],
        evidence_fields_used=suggestion.get("evidence_fields_used", []),
        status=suggestion["status"],
        can_approve=can_approve,
        unavailability_reason=suggestion.get("unavailability_reason"),
    )


@router.post(
    "/{chargeback_id}/classification/suggestion",
    response_model=ClassificationSuggestionResponse,
)
def suggest_dispute_classification(
    chargeback_id: str,
    payload: ClassificationSuggestionRequest,
) -> ClassificationSuggestionResponse:
    record = store.get_dispute(chargeback_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Dispute not found.")
    ineligibility = _suggestion_ineligibility(record)
    if ineligibility:
        raise HTTPException(
            status_code=409,
            detail=f"Dispute is not eligible for an AI suggestion: {ineligibility}.",
        )
    try:
        minimum_confidence = reason_classification_min_confidence()
    except ReasonClassificationConfigError as exc:
        raise HTTPException(
            status_code=503,
            detail="Reason classification configuration is unavailable.",
        ) from exc

    state = record["state"]
    existing = state.get("classification_suggestion")
    if existing and existing.get("status") == "pending":
        return _suggestion_response(existing, minimum_confidence=minimum_confidence)
    if not reason_classification_enabled():
        raise HTTPException(status_code=503, detail="AI reason classification is disabled.")

    network = state["card_network"]
    candidates = reason_classification_candidates(network)
    if not candidates:
        raise HTTPException(
            status_code=503,
            detail="No verified reason-code candidates are available for this card network.",
        )
    try:
        result, model = generate_reason_recommendation(state, candidates)
    except (ReasonClassificationConfigError, ReasonClassificationRequestError) as exc:
        raise HTTPException(
            status_code=503,
            detail="AI reason classification is temporarily unavailable; use manual classification.",
        ) from exc

    allowed_codes = {candidate["reason_code"] for candidate in candidates}
    code = result.recommended_reason_code
    if code is not None and (
        code not in allowed_codes or not _playbook_available(network, code)
    ):
        raise HTTPException(
            status_code=503,
            detail="AI reason classification returned an invalid recommendation; use manual classification.",
        )
    if result.cannot_classify:
        status = "unavailable"
        unavailable_reason = "model_could_not_classify"
    elif result.confidence < minimum_confidence:
        status = "unavailable"
        unavailable_reason = "confidence_below_threshold"
    else:
        status = "pending"
        unavailable_reason = None
    suggestion = {
        "suggestion_id": f"rcs_{uuid4().hex}",
        "card_network": network,
        "recommended_reason_code": code,
        "confidence": result.confidence,
        "rationale": result.rationale,
        "evidence_fields_used": result.evidence_fields_used,
        "model": model,
        "prompt_schema_version": PROMPT_SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc),
        "requested_by_actor_id": payload.actor_id,
        "status": status,
        "unavailability_reason": unavailable_reason,
    }
    saved = store.save_classification_suggestion(chargeback_id, suggestion)
    if saved is None:
        raise HTTPException(
            status_code=409,
            detail="Dispute classification changed while the suggestion was generated.",
        )
    return _suggestion_response(saved, minimum_confidence=minimum_confidence)


@router.post(
    "/{chargeback_id}/classification/suggestion/reject",
    response_model=ClassificationSuggestionResponse,
)
def reject_dispute_classification_suggestion(
    chargeback_id: str,
    payload: ClassificationSuggestionRejectRequest,
) -> ClassificationSuggestionResponse:
    if store.get_dispute(chargeback_id) is None:
        raise HTTPException(status_code=404, detail="Dispute not found.")
    try:
        suggestion = store.reject_classification_suggestion(
            chargeback_id,
            suggestion_id=payload.suggestion_id,
            actor_id=payload.actor_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if suggestion is None:
        raise HTTPException(status_code=409, detail="Classification suggestion is no longer pending.")
    try:
        minimum_confidence = reason_classification_min_confidence()
    except ReasonClassificationConfigError:
        minimum_confidence = 1.0
    return _suggestion_response(suggestion, minimum_confidence=minimum_confidence)


@router.post(
    "/{chargeback_id}/classification",
    response_model=DisputeClassificationResponse,
)
def classify_dispute(
    chargeback_id: str,
    payload: DisputeClassificationRequest,
    background_tasks: BackgroundTasks,
) -> DisputeClassificationResponse:
    record = store.get_dispute(chargeback_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Dispute not found.")
    existing_network = record["state"].get("card_network")
    if existing_network and existing_network != payload.card_network:
        raise HTTPException(
            status_code=409,
            detail="Card network conflicts with verified payment data.",
        )
    if not _playbook_available(payload.card_network, payload.network_reason_code):
        raise HTTPException(
            status_code=422,
            detail="No supported playbook and rebuttal template exist for this classification.",
        )
    minimum_suggestion_confidence = None
    if payload.suggestion_id is not None:
        try:
            minimum_suggestion_confidence = reason_classification_min_confidence()
        except ReasonClassificationConfigError as exc:
            raise HTTPException(
                status_code=503,
                detail="Reason classification configuration is unavailable.",
            ) from exc
    try:
        state = store.claim_dispute_classification(
            chargeback_id,
            card_network=payload.card_network,
            network_reason_code=payload.network_reason_code,
            actor_id=payload.actor_id,
            suggestion_id=payload.suggestion_id,
            minimum_suggestion_confidence=minimum_suggestion_confidence,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if state is None:
        raise HTTPException(
            status_code=409,
            detail="Dispute is not eligible for classification resume or was already scheduled.",
        )
    background_tasks.add_task(run_chargeback_graph, state)
    return DisputeClassificationResponse(
        chargeback_id=chargeback_id,
        status="scheduled",
        card_network=payload.card_network,
        network_reason_code=payload.network_reason_code,
    )


@router.post("/{chargeback_id}/outcome", response_model=OutcomeResponse)
def record_dispute_outcome(
    chargeback_id: str,
    payload: OutcomeUpdate,
) -> OutcomeResponse:
    record = store.get_dispute(chargeback_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Dispute not found.")
    if record["status"] != "completed":
        raise HTTPException(status_code=409, detail="Dispute processing is not complete.")

    state = record["state"]
    try:
        state, _ = record_adjudicated_outcome(
            state,
            payload.outcome,
            payload.reason,
        )
    except (OutcomeConflictError, OutcomeNotEligibleError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    store.update_dispute(chargeback_id, status="completed", state=state)
    return OutcomeResponse(
        chargeback_id=chargeback_id,
        final_outcome=state["final_outcome"],
        outcome_reason=state["outcome_reason"],
        outcome_recorded_at=state["outcome_recorded_at"],
    )
