import logging
import os
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from core.state import ChargebackState, CommsEvidence
from integrations.freshdesk import FreshdeskClient
from integrations.gmail_reader import GmailReader


logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _customer_email(state: ChargebackState) -> str:
    transaction = state.get("transaction")
    return transaction["customer_email"] if transaction else ""


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc)
    if isinstance(value, (int, float)) or str(value).isdigit():
        return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(str(value))
        except (TypeError, ValueError):
            logger.warning("Unable to parse communication timestamp %r", value)
            return None
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)


def _stub_email_response(state: ChargebackState) -> list[dict[str, Any]]:
    order_id = state.get("order_id", state["chargeback_id"])
    email = _customer_email(state) or "customer@example.com"
    delivered_at = (
        state["shipping"]["delivered_at"]
        if state.get("shipping") and state["shipping"]["delivered_at"]
        else state["filing_deadline"] - timedelta(days=12)
    )
    return [
        {
            "from": state["merchant_profile"]["name"],
            "to": email,
            "subject": f"Order {order_id} confirmation",
            "direction": "outbound",
            "timestamp": (delivered_at - timedelta(days=3)).isoformat(),
        },
        {
            "from": email,
            "to": state["merchant_profile"]["name"],
            "subject": f"Thanks for the update on {order_id}",
            "direction": "inbound",
            "timestamp": (delivered_at + timedelta(hours=2)).isoformat(),
        },
    ]


def _gmail_headers(message: dict[str, Any]) -> dict[str, str]:
    headers = (message.get("payload") or {}).get("headers") or []
    return {
        str(header.get("name", "")).lower(): str(header.get("value", ""))
        for header in headers
    }


def _normalize_gmail_message(
    message: dict[str, Any],
    customer_email: str,
) -> dict[str, Any]:
    headers = _gmail_headers(message)
    sender = headers.get("from", "")
    return {
        "id": message.get("id"),
        "thread_id": message.get("threadId"),
        "from": sender,
        "to": headers.get("to", ""),
        "subject": headers.get("subject", ""),
        "direction": "inbound" if customer_email.lower() in sender.lower() else "outbound",
        "timestamp": message.get("internalDate") or headers.get("date"),
        "snippet": message.get("snippet", ""),
        "raw": message,
    }


def _collect_gmail(state: ChargebackState) -> list[dict[str, Any]]:
    order_id = state.get("order_id")
    if not order_id:
        raise ValueError("Gmail collection requires order_id")
    customer_email = _customer_email(state)
    query = f'"{order_id}"'
    if customer_email:
        query = f'{query} (from:{customer_email} OR to:{customer_email})'
    messages = GmailReader.from_env().search_messages(query)
    return [_normalize_gmail_message(message, customer_email) for message in messages]


def _collect_freshdesk(state: ChargebackState) -> list[dict[str, Any]]:
    customer_email = _customer_email(state)
    if not customer_email:
        raise ValueError("Freshdesk collection requires customer email")
    order_id = state.get("order_id", "").lower()
    tickets = FreshdeskClient.from_env().search_tickets(email=customer_email)
    if not order_id:
        return tickets
    return [
        ticket
        for ticket in tickets
        if order_id in str(ticket.get("subject", "")).lower()
        or order_id in str(ticket.get("description_text", "")).lower()
        or order_id in str(ticket.get("custom_fields", {})).lower()
    ]


def _event_timestamp(event: dict[str, Any]) -> datetime | None:
    return _parse_datetime(
        event.get("timestamp")
        or event.get("created_at")
        or event.get("internalDate")
    )


def _build_comms_evidence(
    state: ChargebackState,
    emails: list[dict[str, Any]],
    support_tickets: list[dict[str, Any]],
    *,
    source_errors: dict[str, str] | None = None,
) -> CommsEvidence:
    delivered_at = state["shipping"]["delivered_at"] if state.get("shipping") else None
    received_at = state.get("chargeback_received_at")

    post_delivery_interaction = False
    if delivered_at:
        customer_events = [
            email for email in emails if email.get("direction") == "inbound"
        ] + support_tickets
        post_delivery_interaction = any(
            timestamp is not None and timestamp > delivered_at
            for timestamp in (_event_timestamp(event) for event in customer_events)
        )

    explicit_prior_complaint = any(
        bool(ticket.get("raised_before_chargeback"))
        or ticket.get("type") == "pre_chargeback_complaint"
        for ticket in support_tickets
    )
    timed_prior_complaint = False
    if received_at:
        timed_prior_complaint = any(
            timestamp is not None and timestamp < received_at
            for timestamp in (_event_timestamp(ticket) for ticket in support_tickets)
        )

    return {
        "emails": emails,
        "support_tickets": support_tickets,
        "post_delivery_interaction": post_delivery_interaction,
        "complaint_raised_before_chargeback": (
            explicit_prior_complaint or timed_prior_complaint
        ),
        "raw": {
            "source": "gmail_freshdesk",
            "source_errors": source_errors or {},
            "freshdesk_domain": state["merchant_profile"].get("freshdesk_domain"),
        },
    }


def _collect_communications(
    state: ChargebackState,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    if _env_flag("CHARGEGUARD_USE_STUBS"):
        return _stub_email_response(state), [], {}

    errors: dict[str, str] = {}
    try:
        emails = _collect_gmail(state)
    except Exception as exc:
        logger.warning("Gmail evidence collection failed: %s", exc)
        emails = []
        errors["gmail"] = str(exc)

    try:
        tickets = _collect_freshdesk(state)
    except Exception as exc:
        logger.warning("Freshdesk evidence collection failed: %s", exc)
        tickets = []
        errors["freshdesk"] = str(exc)
    return emails, tickets, errors


def comms_agent(state: ChargebackState) -> ChargebackState:
    """Collect and correlate customer communication evidence."""
    logger.info("Running comms evidence agent for %s", state["chargeback_id"])
    try:
        emails, tickets, errors = _collect_communications(state)
        state["comms"] = _build_comms_evidence(
            state,
            emails,
            tickets,
            source_errors=errors,
        )
    except Exception as exc:
        logger.exception("Communication evidence processing failed")
        state["comms"] = {
            "emails": [],
            "support_tickets": [],
            "post_delivery_interaction": False,
            "complaint_raised_before_chargeback": False,
            "raw": {"source": "comms_agent_empty", "error": str(exc)},
        }
    return state
