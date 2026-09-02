from collections.abc import Callable
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from agents.escalation import human_escalation_agent
from api.store import store
from api.webhooks import build_initial_state
from core.outcomes import (
    OutcomeConflictError,
    OutcomeNotEligibleError,
    record_adjudicated_outcome,
)
from core.state import ChargebackState, MerchantProfile
from integrations.razorpay import (
    RazorpayClient,
    RazorpayConfigError,
    RazorpayRequestError,
)
from integrations.razorpay_schemas import (
    NormalizedRazorpayDispute,
    RazorpayWebhookEnvelope,
)
from integrations.razorpay_webhook import normalize_dispute


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_CREATE_EVENT = "payment.dispute.created"
_TERMINAL_EVENTS = {
    "payment.dispute.won",
    "payment.dispute.lost",
    "payment.dispute.closed",
}


def simulator_metadata_allowed(envelope: RazorpayWebhookEnvelope) -> bool:
    if os.getenv("ENVIRONMENT", "development").strip().lower() == "production":
        return False
    enabled = os.getenv("RAZORPAY_SIMULATOR_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    payment = envelope.payload.payment.entity if envelope.payload.payment else None
    return bool(
        enabled
        and envelope.payload.dispute.entity.id.startswith("disp_SIM_")
        and payment
        and payment.notes.get("chargeguard_simulator") is True
    )


def normalize_with_enrichment(
    envelope: RazorpayWebhookEnvelope,
    webhook_event_id: str,
    *,
    client_factory: Callable[[], RazorpayClient] | None = None,
) -> NormalizedRazorpayDispute:
    client_factory = client_factory or RazorpayClient.from_env
    payment = envelope.payload.payment.entity if envelope.payload.payment else None
    needs_payment = (
        payment is None
        or not payment.method
        or not payment.order_id
        or (
            payment.method.lower() == "card"
            and (payment.card is None or not payment.card.network)
        )
    )
    enriched_payment = None
    failure_reason = None
    if needs_payment:
        try:
            enriched_payment = client_factory().get_payment(
                envelope.payload.dispute.entity.payment_id,
                expand_card=True,
            )
        except (RazorpayConfigError, RazorpayRequestError) as exc:
            failure_reason = str(exc)
    return normalize_dispute(
        envelope,
        webhook_event_id=webhook_event_id,
        enriched_payment=enriched_payment,
        enrichment_failure_reason=failure_reason,
        allow_simulator_metadata=simulator_metadata_allowed(envelope),
    )


def _playbook_available(network: str | None, reason_code: str | None) -> bool:
    if not network or not reason_code:
        return False
    filename = {
        "VISA": "visa_playbooks.json",
        "MASTERCARD": "mastercard_playbooks.json",
        "RUPAY": "rupay_playbooks.json",
    }.get(network)
    if filename is None:
        return False
    path = PROJECT_ROOT / "documents" / "playbooks" / filename
    try:
        playbooks = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return reason_code in playbooks


def _manual_review_reasons(normalized: NormalizedRazorpayDispute) -> list[str]:
    reasons: list[str] = []
    if normalized.enrichment_degraded:
        reasons.append("razorpay_payment_enrichment_failed")
    if normalized.payment_rail != "CARD":
        reasons.append(f"unsupported_payment_rail:{normalized.payment_rail or 'UNKNOWN'}")
    if not normalized.card_network:
        reasons.append("card_network_unavailable")
    if not normalized.network_reason_code:
        reasons.append("network_reason_code_unavailable")
    if not _playbook_available(
        normalized.card_network,
        normalized.network_reason_code,
    ):
        reasons.append("network_playbook_unavailable")
    if not normalized.order_id:
        reasons.append("order_id_unavailable")
    if normalized.filing_deadline is None:
        reasons.append("respond_by_unavailable")
    elif normalized.deadline_overdue:
        reasons.append("respond_by_overdue")
    return list(dict.fromkeys(reasons))


def _apply_provider_metadata(
    state: ChargebackState,
    normalized: NormalizedRazorpayDispute,
) -> bool:
    incoming_outcome = {
        "payment.dispute.won": "WIN",
        "payment.dispute.lost": "LOSS",
    }.get(normalized.provider_event)
    if (
        state.get("final_outcome") in {"WIN", "LOSS"}
        and incoming_outcome is not None
        and incoming_outcome != state.get("final_outcome")
    ):
        return False
    current_event = state.get("provider_event")
    current_timestamp = state.get("provider_event_timestamp")
    incoming_timestamp = normalized.provider_event_timestamp
    if current_event in _TERMINAL_EVENTS and normalized.provider_event not in _TERMINAL_EVENTS:
        return False
    if (
        current_timestamp is not None
        and incoming_timestamp is not None
        and incoming_timestamp < current_timestamp
        and normalized.provider_event not in _TERMINAL_EVENTS
    ):
        return False
    if current_event == "payment.dispute.closed" and normalized.provider_event in {
        "payment.dispute.won",
        "payment.dispute.lost",
    }:
        return False

    state["provider"] = "razorpay"
    state["provider_dispute_id"] = normalized.provider_dispute_id
    state["provider_event_id"] = normalized.webhook_event_id
    state["webhook_event_id"] = normalized.webhook_event_id
    state["provider_event"] = normalized.provider_event
    if normalized.provider_event_timestamp is not None:
        state["provider_event_timestamp"] = normalized.provider_event_timestamp
    state["provider_account_id"] = normalized.provider_account_id
    state["provider_status"] = normalized.provider_status
    state["provider_dispute_status"] = normalized.provider_status
    state["provider_phase"] = normalized.provider_phase
    state["provider_reason_code"] = normalized.provider_reason_code
    if normalized.network_reason_code:
        state["network_reason_code"] = normalized.network_reason_code
    if normalized.payment_rail:
        state["payment_rail"] = normalized.payment_rail
    state["deadline_overdue"] = normalized.deadline_overdue
    if normalized.filing_deadline is not None:
        state["provider_respond_by"] = normalized.filing_deadline
    if normalized.card_network:
        state["card_network"] = normalized.card_network
    if normalized.order_id:
        state["order_id"] = normalized.order_id
    return True


def _new_state(
    normalized: NormalizedRazorpayDispute,
    merchant: MerchantProfile,
    manual_reasons: list[str],
) -> ChargebackState:
    received_at = normalized.provider_event_timestamp or datetime.now(timezone.utc)
    deadline = normalized.filing_deadline or received_at
    state = build_initial_state(
        chargeback_id=normalized.chargeback_id,
        order_id=normalized.order_id,
        payment_id=normalized.payment_id,
        reason_code=normalized.network_reason_code or "",
        card_network=normalized.card_network,
        dispute_amount=float(normalized.dispute_amount),
        currency=normalized.currency,
        filing_deadline=deadline,
        merchant_profile=merchant,
        received_at=received_at,
        evidence_collection_degraded=bool(manual_reasons),
        degraded_reasons=manual_reasons,
    )
    _apply_provider_metadata(state, normalized)
    return state


def process_normalized_dispute(
    normalized: NormalizedRazorpayDispute,
    merchant: MerchantProfile,
    schedule_graph: Callable[[ChargebackState], None],
) -> dict[str, Any]:
    existing = store.get_dispute(normalized.chargeback_id)
    manual_reasons = _manual_review_reasons(normalized)

    if existing is None:
        state = _new_state(normalized, merchant, manual_reasons)
        should_schedule = normalized.provider_event == _CREATE_EVENT and not manual_reasons
        if not should_schedule:
            state["decision"] = "ESCALATE_DEGRADED"
            state["requires_human_review"] = True
            state["decision_reasoning"] = (
                "Razorpay dispute requires human review: " + ", ".join(manual_reasons)
                if manual_reasons
                else f"Received {normalized.provider_event} before dispute creation."
            )
            if not manual_reasons:
                state["evidence_collection_degraded"] = True
                state["degraded_reasons"] = ["out_of_order_provider_event"]
            state = human_escalation_agent(state)
        if not store.create_dispute(state):
            existing = store.get_dispute(normalized.chargeback_id)
        else:
            if should_schedule:
                schedule_graph(state)
                return {"status": "scheduled", "chargeback_id": normalized.chargeback_id}
            store.update_dispute(
                normalized.chargeback_id,
                status="completed",
                state=state,
            )
            return {
                "status": "manual_review",
                "chargeback_id": normalized.chargeback_id,
            }

    if existing is None:
        raise RuntimeError("Dispute disappeared after provider upsert.")

    state = existing["state"]
    same_created_event = (
        normalized.provider_event == _CREATE_EVENT
        and state.get("provider_event_id") == normalized.webhook_event_id
    )
    metadata_updated = _apply_provider_metadata(state, normalized)
    if (
        same_created_event
        and metadata_updated
        and not manual_reasons
        and existing["status"] in {"received", "processing", "failed"}
        and state.get("decision") is None
    ):
        schedule_graph(state)
        return {"status": "scheduled", "chargeback_id": normalized.chargeback_id}
    if metadata_updated and normalized.provider_event == "payment.dispute.action_required":
        state["provider_action_required"] = True
        state["requires_human_review"] = True
    elif metadata_updated and normalized.provider_event == "payment.dispute.under_review":
        state["provider_action_required"] = False

    learned = False
    outcome_not_eligible = False
    outcome = {
        "payment.dispute.won": "WIN",
        "payment.dispute.lost": "LOSS",
    }.get(normalized.provider_event)
    if outcome:
        try:
            state, learned = record_adjudicated_outcome(
                state,
                outcome,
                f"Razorpay dispute marked {outcome.lower()}.",
            )
        except OutcomeNotEligibleError:
            outcome_not_eligible = True
        except OutcomeConflictError:
            metadata_updated = False

    store.update_dispute(
        normalized.chargeback_id,
        status=existing["status"],
        state=state,
        error=existing.get("error"),
    )
    return {
        "status": (
            "outcome_not_eligible"
            if outcome_not_eligible
            else "updated"
            if metadata_updated or learned
            else "stale"
        ),
        "chargeback_id": normalized.chargeback_id,
    }
