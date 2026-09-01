"""Development-only generator for signed Razorpay-shaped dispute events."""

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
import secrets
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from api.auth import require_api_key
from api.schemas import RazorpaySimulatorCreate, RazorpaySimulatorTransition
from api.store import store


router = APIRouter(
    prefix="/dev/razorpay-simulator",
    tags=["razorpay-simulator"],
    dependencies=[Depends(require_api_key)],
)
_EVENTS = {
    "action_required": "payment.dispute.action_required",
    "under_review": "payment.dispute.under_review",
    "won": "payment.dispute.won",
    "lost": "payment.dispute.lost",
    "closed": "payment.dispute.closed",
}
_ALLOWED_TRANSITIONS = {
    "open": {"action_required", "under_review", "closed"},
    "action_required": {"under_review", "closed"},
    "under_review": {"won", "lost", "closed"},
    "won": {"closed"},
    "lost": {"closed"},
}


def _simulator_enabled() -> bool:
    value = os.getenv("RAZORPAY_SIMULATOR_ENABLED")
    if value is None:
        value = os.getenv("CHARGEBACK_SIMULATOR_ENABLED", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _require_simulator() -> None:
    if os.getenv("ENVIRONMENT", "development").strip().lower() == "production" or not _simulator_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Simulator is unavailable.")


def _secret() -> str:
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Webhook secret is not configured.")
    return secret


def build_simulator_envelope(record: dict[str, Any], event_name: str, state: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    dispute_status = {"action_required": "open", "under_review": "under_review", "won": "won", "lost": "lost", "closed": "closed"}.get(state, "open")
    notes = {
        "chargeguard_simulator": True,
        "chargeguard_network_reason_code": record.get("network_reason_code"),
    }
    if record.get("card_network"):
        notes["chargeguard_card_network"] = record["card_network"]
    payment = {
        "id": record["payment_id"], "entity": "payment", "amount": record["payment_amount_paise"],
        "currency": record["currency"], "status": "captured", "order_id": record["order_id"],
        "method": record["method"], "captured": True,
        "email": record.get("customer_email"), "contact": record.get("customer_contact"),
        "notes": notes, "created_at": int(record["created_at"].timestamp()),
    }
    if record["method"] == "card" and record.get("card_network"):
        payment["card"] = {"network": record["card_network"]}
    if record["method"] == "upi":
        payment["vpa"] = record.get("vpa")
    return {
        "entity": "event",
        "account_id": record["account_id"],
        "event": event_name,
        "contains": ["payment", "dispute"],
        "payload": {
            "payment": {"entity": payment},
            "dispute": {"entity": {
                "id": record["dispute_id"], "entity": "dispute", "payment_id": record["payment_id"],
                "amount": record["dispute_amount_paise"], "currency": record["currency"],
                "amount_deducted": record["dispute_amount_paise"], "reason_code": record["razorpay_reason_code"],
                "respond_by": int(record["respond_by"].timestamp()), "status": dispute_status, "phase": "chargeback",
                "evidence": {"amount": record["dispute_amount_paise"], "submitted_at": int(now.timestamp()) if state == "under_review" else None},
                "created_at": int(record["created_at"].timestamp()),
            }},
        },
        "created_at": int(now.timestamp()),
    }


def deliver_simulator_event(
    target_url: str,
    body: bytes,
    event_id: str,
    webhook_secret: str,
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    parsed_target = urlparse(target_url)
    if (
        parsed_target.scheme not in {"http", "https"}
        or parsed_target.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed_target.path != "/webhook/razorpay"
    ):
        raise ValueError("Razorpay simulator target must be a loopback HTTP(S) URL.")
    signature = hmac.new(webhook_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Razorpay-Signature": signature,
        "x-razorpay-event-id": event_id,
    }
    if client is not None:
        response = client.post(target_url, content=body, headers=headers)
    else:
        with httpx.Client(timeout=10.0) as http_client:
            response = http_client.post(target_url, content=body, headers=headers)
    return {"status_code": response.status_code, "body": response.text, "signature": signature}


def _deliver(record: dict[str, Any], event_name: str, state: str) -> dict[str, Any]:
    event_id = "evt_SIM_" + secrets.token_urlsafe(16)
    envelope = build_simulator_envelope(record, event_name, state)
    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    delivery = deliver_simulator_event(
        os.getenv("RAZORPAY_SIMULATOR_TARGET_URL", "http://127.0.0.1:8000/webhook/razorpay"),
        body,
        event_id,
        _secret(),
    )
    return {"event_id": event_id, "event_name": event_name, "delivery": delivery, "payload_sha256": hashlib.sha256(body).hexdigest()}


def _safe_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.items()
        if key not in {"customer_email", "customer_contact", "vpa"}
    }


@router.post("/disputes")
def create_simulated_dispute(payload: RazorpaySimulatorCreate) -> dict[str, Any]:
    _require_simulator()
    merchant = store.get_merchant(payload.merchant_id)
    if merchant is None:
        raise HTTPException(status_code=404, detail="Merchant not found.")
    if merchant.get("payment_provider") != "razorpay" or not merchant.get("razorpay_account_id"):
        raise HTTPException(status_code=422, detail="Merchant must have a Razorpay provider and account ID.")
    if payload.dispute_amount_paise > payload.payment_amount_paise:
        raise HTTPException(status_code=422, detail="Dispute amount cannot exceed payment amount.")
    if payload.method == "card" and payload.card_network is None:
        raise HTTPException(status_code=422, detail="Card simulations require card_network.")
    now = datetime.now(timezone.utc)
    record = {
        **payload.model_dump(),
        "dispute_id": "disp_SIM_" + secrets.token_urlsafe(16),
        "account_id": merchant["razorpay_account_id"],
        "state": "open",
        "created_at": now,
        "respond_by": now + timedelta(hours=payload.respond_within_hours),
        "deliveries": [],
    }
    store.create_simulator_dispute(record)
    result = _deliver(record, "payment.dispute.created", "open")
    record["deliveries"].append(result)
    store.update_simulator_dispute(record["dispute_id"], deliveries=record["deliveries"])
    return {"dispute_id": record["dispute_id"], **result}


@router.post("/disputes/{dispute_id}/transition")
def transition_simulated_dispute(dispute_id: str, payload: RazorpaySimulatorTransition) -> dict[str, Any]:
    _require_simulator()
    record = store.get_simulator_dispute(dispute_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Simulated dispute not found.")
    if not payload.force and payload.state not in _ALLOWED_TRANSITIONS.get(record["state"], set()):
        raise HTTPException(status_code=409, detail="Invalid simulated dispute transition.")
    result = _deliver(record, _EVENTS[payload.state], payload.state)
    record["deliveries"].append(result)
    store.update_simulator_dispute(dispute_id, state=payload.state, deliveries=record["deliveries"])
    return {"dispute_id": dispute_id, "state": payload.state, **result}


@router.get("/disputes")
def list_simulated_disputes() -> list[dict[str, Any]]:
    _require_simulator()
    return [_safe_record(record) for record in store.list_simulator_disputes()]


@router.get("/events")
def list_simulator_events() -> list[dict[str, Any]]:
    _require_simulator()
    return [
        {key: value for key, value in event.items() if key not in {"payload", "customer_email", "contact", "vpa"}}
        for event in store.list_provider_events()
        if event.get("provider") == "razorpay"
    ]
