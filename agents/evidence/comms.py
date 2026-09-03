import logging
import os
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from core.state import ChargebackState, CommsEvidence
from integrations.freshdesk import FreshdeskClient, FreshdeskConfigError
from integrations.gmail_reader import GmailConfigError, GmailReader


logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _comms_use_stubs(provider: str) -> bool:
    override_name = "GMAIL_USE_STUBS" if provider == "gmail" else "FRESHDESK_USE_STUBS"
    value = os.getenv(override_name)
    if value is None:
        return _env_flag("CHARGEGUARD_USE_STUBS")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _customer_email(state: ChargebackState) -> str:
    transaction = state.get("transaction")
    return transaction["customer_email"] if transaction else ""


def _commerce_references(state: ChargebackState) -> list[str]:
    references = list(
        dict.fromkeys(
            value
            for value in (
                state.get("commerce_order_number"),
                state.get("commerce_order_id"),
            )
            if value
        )
    )
    if not references and state.get("provider") != "razorpay" and state.get("order_id"):
        references.append(state["order_id"])
    return references


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
    references = _commerce_references(state)
    order_id = references[0] if references else state.get("order_id", state["chargeback_id"])
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
    references = _commerce_references(state)
    if not references:
        raise ValueError("Gmail collection requires a commerce order reference")
    customer_email = _customer_email(state)
    if not customer_email:
        raise ValueError("Gmail collection requires customer email")
    query = "(" + " OR ".join(f'\"{reference}\"' for reference in references) + ")"
    if customer_email:
        query = f'{query} (from:{customer_email} OR to:{customer_email})'
    merchant = state["merchant_profile"]
    messages = GmailReader.from_env(
        connector_ref=merchant.get("support_connector_ref"),
        user_id=merchant.get("gmail_user_id"),
    ).search_messages(query)
    return [_normalize_gmail_message(message, customer_email) for message in messages]


def _collect_freshdesk(state: ChargebackState) -> list[dict[str, Any]]:
    customer_email = _customer_email(state)
    if not customer_email:
        raise ValueError("Freshdesk collection requires customer email")
    references = [reference.casefold() for reference in _commerce_references(state)]
    if not references:
        raise ValueError("Freshdesk collection requires a commerce order reference")
    merchant = state["merchant_profile"]
    tickets = FreshdeskClient.from_env(
        connector_ref=merchant.get("support_connector_ref"),
        domain=merchant.get("freshdesk_domain") or None,
    ).search_tickets(email=customer_email)
    return [
        ticket
        for ticket in tickets
        if any(
            reference in str(ticket.get(field, "")).casefold()
            for reference in references
            for field in ("subject", "description_text", "custom_fields")
        )
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
    if not _commerce_references(state):
        raise ValueError("Communication collection requires a commerce order reference")
    if _comms_use_stubs("gmail") and _comms_use_stubs("freshdesk"):
        return _stub_email_response(state), [], {}

    errors: dict[str, str] = {}
    if _comms_use_stubs("gmail"):
        emails = _stub_email_response(state)
    else:
        try:
            emails = _collect_gmail(state)
        except GmailConfigError:
            logger.warning("Gmail credentials are unavailable")
            emails = []
            errors["gmail_credentials_missing"] = "gmail_credentials_missing"
        except Exception:
            logger.warning("Gmail evidence collection failed")
            emails = []
            errors["gmail_provider_unavailable"] = "gmail_provider_unavailable"

    if _comms_use_stubs("freshdesk"):
        tickets = []
    else:
        try:
            tickets = _collect_freshdesk(state)
        except FreshdeskConfigError:
            logger.warning("Freshdesk credentials are unavailable")
            tickets = []
            errors["freshdesk_credentials_missing"] = "freshdesk_credentials_missing"
        except Exception:
            logger.warning("Freshdesk evidence collection failed")
            tickets = []
            errors["freshdesk_provider_unavailable"] = "freshdesk_provider_unavailable"
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
        for reason in errors:
            if reason in errors:
                state["evidence_collection_degraded"] = True
                degraded_reasons = state.setdefault("degraded_reasons", [])
                if reason not in degraded_reasons:
                    degraded_reasons.append(reason)
    except Exception:
        logger.error("Communication evidence processing failed")
        state["comms"] = {
            "emails": [],
            "support_tickets": [],
            "post_delivery_interaction": False,
            "complaint_raised_before_chargeback": False,
            "raw": {"source": "comms_agent_empty", "error": "comms_processing_failed"},
        }
        state["evidence_collection_degraded"] = True
        degraded_reasons = state.setdefault("degraded_reasons", [])
        if "comms_processing_failed" not in degraded_reasons:
            degraded_reasons.append("comms_processing_failed")
    return state
