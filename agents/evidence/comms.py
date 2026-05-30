import logging
from typing import Any

from core.state import ChargebackState, CommsEvidence


logger = logging.getLogger(__name__)


def _customer_email(state: ChargebackState) -> str:
    customer_email = ""
    if state.get("transaction"):
        customer_email = state["transaction"]["customer_email"]
    return customer_email


def _empty_comms_evidence(
    state: ChargebackState,
    *,
    error: str | None = None,
) -> CommsEvidence:
    return {
        "emails": [],
        "support_tickets": [],
        "post_delivery_interaction": False,
        "complaint_raised_before_chargeback": False,
        "raw": {
            "source": "comms_agent_empty",
            "error": error,
            "freshdesk_domain": state["merchant_profile"].get("freshdesk_domain"),
        },
    }


def _stub_email_response(state: ChargebackState) -> list[dict[str, Any]]:
    order_id = state.get("order_id", state["chargeback_id"])
    email = _customer_email(state) or "customer@example.com"

    return [
        {
            "from": state["merchant_profile"]["name"],
            "to": email,
            "subject": f"Order {order_id} confirmation",
            "direction": "outbound",
            "category": "order_confirmation",
        },
        {
            "from": email,
            "to": state["merchant_profile"]["name"],
            "subject": f"Thanks for the update on {order_id}",
            "direction": "inbound",
            "category": "post_delivery_interaction",
        },
    ]


def _stub_ticket_response(state: ChargebackState) -> list[dict[str, Any]]:
    return []


def _build_comms_evidence(
    state: ChargebackState,
    emails: list[dict[str, Any]],
    support_tickets: list[dict[str, Any]],
) -> CommsEvidence:
    post_delivery_interaction = any(
        email.get("category") == "post_delivery_interaction" or email.get("direction") == "inbound"
        for email in emails
    )
    complaint_raised_before_chargeback = any(
        bool(ticket.get("raised_before_chargeback")) or ticket.get("type") == "pre_chargeback_complaint"
        for ticket in support_tickets
    )

    evidence: CommsEvidence = {
        "emails": emails,
        "support_tickets": support_tickets,
        "post_delivery_interaction": post_delivery_interaction,
        "complaint_raised_before_chargeback": complaint_raised_before_chargeback,
        "raw": {
            "source": "comms_agent_stub",
            "freshdesk_domain": state["merchant_profile"].get("freshdesk_domain"),
        },
    }
    return evidence


def comms_agent(state: ChargebackState) -> ChargebackState:
    """Collect customer communication and support-ticket evidence."""
    logger.info("Running comms evidence agent for %s", state["chargeback_id"])

    try:
        emails = _stub_email_response(state)
        support_tickets = _stub_ticket_response(state)
        state["comms"] = _build_comms_evidence(state, emails, support_tickets)
    except Exception as exc:
        logger.exception("Comms evidence collection failed")
        state["comms"] = _empty_comms_evidence(state, error=str(exc))

    return state
