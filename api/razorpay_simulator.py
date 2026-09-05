"""Development-only generator for signed Razorpay-shaped dispute events."""

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from api.auth import require_api_key
from api.schemas import (
    RazorpaySimulatorCreate,
    RazorpaySimulatorScenarioRun,
    RazorpaySimulatorTransition,
)
from api.simulation_scenarios import (
    get_simulation_scenario,
    list_simulation_scenarios,
)
from api.store import OrderIdentifierConflictError, store
from core.state import OrderRecord


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


def _prepared_event(
    record: dict[str, Any],
    event_name: str,
    state: str,
) -> tuple[str, bytes]:
    event_id = "evt_SIM_" + secrets.token_urlsafe(16)
    envelope = build_simulator_envelope(record, event_name, state)
    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    return event_id, body


def _deliver_prepared(
    event_id: str,
    body: bytes,
    *,
    webhook_secret: str | None = None,
) -> dict[str, Any]:
    delivery = deliver_simulator_event(
        os.getenv("RAZORPAY_SIMULATOR_TARGET_URL", "http://127.0.0.1:8000/webhook/razorpay"),
        body,
        event_id,
        webhook_secret if webhook_secret is not None else _secret(),
    )
    return {
        "event_id": event_id,
        "delivery": {
            key: value for key, value in delivery.items() if key != "signature"
        },
        "payload_sha256": hashlib.sha256(body).hexdigest(),
    }


def _deliver(record: dict[str, Any], event_name: str, state: str) -> dict[str, Any]:
    event_id, body = _prepared_event(record, event_name, state)
    return {
        "event_name": event_name,
        **_deliver_prepared(event_id, body),
    }


def _safe_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.items()
        if key not in {"customer_email", "customer_contact", "vpa"}
    }


def _validate_create(
    payload: RazorpaySimulatorCreate,
    merchant: dict[str, Any] | None,
) -> dict[str, Any]:
    if merchant is None:
        raise HTTPException(status_code=404, detail="Merchant not found.")
    if merchant.get("payment_provider") != "razorpay" or not merchant.get("razorpay_account_id"):
        raise HTTPException(status_code=422, detail="Merchant must have a Razorpay provider and account ID.")
    if payload.dispute_amount_paise > payload.payment_amount_paise:
        raise HTTPException(status_code=422, detail="Dispute amount cannot exceed payment amount.")
    if payload.method == "card" and payload.card_network is None:
        raise HTTPException(status_code=422, detail="Card simulations require card_network.")
    return merchant


def _seed_simulator_order(record: dict[str, Any]) -> None:
    """Give the real graph an exact merchant-owned order to correlate."""
    merchant_id = record["merchant_id"]
    suffix = record["dispute_id"].removeprefix("disp_SIM_")
    scenario = get_simulation_scenario(record.get("scenario_id", ""))
    profile = (scenario.get("device") or {}) if scenario else {}
    order: OrderRecord = {
        "order_id": record["order_id"],
        "merchant_id": merchant_id,
        "customer_email": record.get("customer_email") or f"simulator+{suffix}@example.test",
        "customer_ip": profile.get("ip", "192.0.2.10"),
        "user_agent": "ChargeGuard-Simulator/1.0",
        "shipping_address": "Synthetic simulator address, Bengaluru",
        "order_date": record["created_at"],
        "is_disputed": False,
        "is_fraud_flagged": False,
        "payment_provider": "razorpay",
        "provider_payment_id": record["payment_id"],
        "provider_order_id": record["order_id"],
        "commerce_order_number": f"SIM-{suffix}",
        "tracking_id": f"trk_SIM_{suffix}",
        "fulfillment_id": f"ful_SIM_{suffix}",
    }
    try:
        created = store.create_order(order)
    except OrderIdentifierConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not created:
        raise HTTPException(
            status_code=409,
            detail="Simulator order or payment identifiers were already used; generate new test IDs.",
        )


def _create_record(
    payload: RazorpaySimulatorCreate,
    merchant: dict[str, Any],
    *,
    scenario_id: str | None = None,
    account_id: str | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    record = {
        **payload.model_dump(),
        "dispute_id": "disp_SIM_" + secrets.token_urlsafe(16),
        "account_id": account_id or merchant["razorpay_account_id"],
        "state": "open",
        "created_at": now,
        "respond_by": now + timedelta(hours=payload.respond_within_hours),
        "deliveries": [],
    }
    if scenario_id is not None:
        record["scenario_id"] = scenario_id
    _seed_simulator_order(record)
    if not store.create_simulator_dispute(record):
        raise HTTPException(status_code=409, detail="Simulated dispute already exists.")
    return record


def _append_delivery(
    record: dict[str, Any],
    result: dict[str, Any],
    *,
    state: str | None = None,
) -> None:
    record["deliveries"].append(result)
    updates: dict[str, Any] = {"deliveries": record["deliveries"]}
    if state is not None:
        record["state"] = state
        updates["state"] = state
    store.update_simulator_dispute(record["dispute_id"], **updates)


def _await_provider_event(event_id: str, timeout_seconds: float = 10.0) -> None:
    """Serialize simulator lifecycle events behind processing of the prior event."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        event = store.get_provider_event(event_id)
        if event is not None and event.get("processing_state") not in {
            "received",
            "queued",
            "processing",
        }:
            return
        time.sleep(0.02)
    raise HTTPException(
        status_code=504,
        detail="Timed out waiting for the prior simulated provider event to finish.",
    )


@router.post("/disputes")
def create_simulated_dispute(payload: RazorpaySimulatorCreate) -> dict[str, Any]:
    _require_simulator()
    merchant = _validate_create(payload, store.get_merchant(payload.merchant_id))
    record = _create_record(payload, merchant)
    result = _deliver(record, "payment.dispute.created", "open")
    _append_delivery(record, result)
    return {"dispute_id": record["dispute_id"], "order_seeded": True, **result}


@router.post("/disputes/{dispute_id}/transition")
def transition_simulated_dispute(dispute_id: str, payload: RazorpaySimulatorTransition) -> dict[str, Any]:
    _require_simulator()
    record = store.get_simulator_dispute(dispute_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Simulated dispute not found.")
    if not payload.force and payload.state not in _ALLOWED_TRANSITIONS.get(record["state"], set()):
        raise HTTPException(status_code=409, detail="Invalid simulated dispute transition.")
    if record["deliveries"]:
        _await_provider_event(record["deliveries"][-1]["event_id"])
    result = _deliver(record, _EVENTS[payload.state], payload.state)
    _append_delivery(record, result, state=payload.state)
    return {"dispute_id": dispute_id, "state": payload.state, **result}


@router.get("/scenarios")
def list_scenarios() -> list[dict[str, Any]]:
    _require_simulator()
    return list_simulation_scenarios()


@router.post("/scenarios/{scenario_id}/run")
def run_scenario(
    scenario_id: str,
    payload: RazorpaySimulatorScenarioRun,
) -> dict[str, Any]:
    _require_simulator()
    scenario = get_simulation_scenario(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Simulation scenario not found.")
    create_payload = RazorpaySimulatorCreate.model_validate(
        {
            "merchant_id": payload.merchant_id,
            "payment_id": "pay_SIM_" + secrets.token_urlsafe(12),
            "order_id": "order_SIM_" + secrets.token_urlsafe(12),
            **scenario["payload"],
        }
    )
    merchant = _validate_create(
        create_payload,
        store.get_merchant(create_payload.merchant_id),
    )
    behavior = scenario["behavior"]
    account_id = None
    if behavior == "unknown_account":
        account_id = "acc_SIM_UNKNOWN_" + secrets.token_urlsafe(8)
    record = _create_record(
        create_payload,
        merchant,
        scenario_id=scenario_id,
        account_id=account_id,
    )

    if behavior == "invalid_signature":
        event_id, body = _prepared_event(record, "payment.dispute.created", "open")
        result = {
            "event_name": "payment.dispute.created",
            **_deliver_prepared(event_id, body, webhook_secret=_secret() + "-invalid"),
        }
        _append_delivery(record, result, state="signature_rejected")
    elif behavior == "duplicate":
        event_id, body = _prepared_event(record, "payment.dispute.created", "open")
        for _ in range(2):
            result = {
                "event_name": "payment.dispute.created",
                **_deliver_prepared(event_id, body),
            }
            _append_delivery(record, result)
    elif behavior == "out_of_order_won":
        result = _deliver(record, _EVENTS["won"], "won")
        _append_delivery(record, result, state="won")
    else:
        result = _deliver(record, "payment.dispute.created", "open")
        _append_delivery(record, result)
        next_state = {
            "created_then_action_required": "action_required",
            "created_then_under_review": "under_review",
            "created_then_closed": "closed",
        }.get(behavior)
        if next_state is not None:
            _await_provider_event(result["event_id"])
            result = _deliver(record, _EVENTS[next_state], next_state)
            _append_delivery(record, result, state=next_state)

    return {
        "scenario_id": scenario_id,
        "dispute_id": record["dispute_id"],
        "order_seeded": True,
        "expected": scenario["expected"],
        "deliveries": record["deliveries"],
    }


@router.get("/disputes")
def list_simulated_disputes() -> list[dict[str, Any]]:
    _require_simulator()
    return [_safe_record(record) for record in store.list_simulator_disputes()]


@router.get("/events")
def list_simulator_events() -> list[dict[str, Any]]:
    _require_simulator()
    return [
        {
            key: value
            for key, value in event.items()
            if key not in {"event_data", "payload", "customer_email", "contact", "vpa"}
        }
        for event in store.list_provider_events()
        if event.get("provider") == "razorpay"
    ]
