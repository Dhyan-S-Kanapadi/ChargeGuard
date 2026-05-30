import json
import logging
import os
from pathlib import Path
from typing import Any

from core.state import ChargebackState


logger = logging.getLogger(__name__)


def _output_dir() -> Path:
    return Path(os.getenv("REBUTTAL_OUTPUT_DIR", "./output/rebuttals"))


def _evidence_status(state: ChargebackState) -> dict[str, bool]:
    return {
        "transaction": bool(state.get("transaction")),
        "shipping": bool(state.get("shipping")),
        "device": bool(state.get("device")),
        "comms": bool(state.get("comms")),
        "consortium": bool(state.get("consortium")),
        "delivery_photo": bool(state.get("delivery_photo")),
        "order_timeline": bool(state.get("order_timeline")),
    }


def _strongest_evidence(state: ChargebackState) -> list[str]:
    evidence: list[str] = []
    transaction = state.get("transaction")
    shipping = state.get("shipping")
    device = state.get("device")
    comms = state.get("comms")
    consortium = state.get("consortium")
    delivery_photo = state.get("delivery_photo")
    timeline = state.get("order_timeline")

    if transaction and transaction["three_ds_authenticated"]:
        evidence.append("3DS authentication completed")
    if transaction and transaction["otp_verified"]:
        evidence.append("OTP verification completed")
    if shipping and shipping["status"].upper() == "DELIVERED":
        evidence.append("Shipment marked delivered")
    if shipping and shipping["signature_obtained"]:
        evidence.append("Proof of delivery signature obtained")
    if device and device["fraud_score"] < 40:
        evidence.append("Low device fraud score")
    if comms and comms["post_delivery_interaction"]:
        evidence.append("Customer interacted after delivery")
    if consortium and not consortium["cross_merchant_fraud_history"]:
        evidence.append("No cross-merchant fraud history")
    if delivery_photo and delivery_photo["ai_verified"]:
        evidence.append("Delivery photo verified")
    if timeline and timeline["delivered_at"]:
        evidence.append("Order timeline confirms delivery")

    return evidence


def _rebuttal_sections(state: ChargebackState) -> list[dict[str, str]]:
    strongest = _strongest_evidence(state)
    return [
        {
            "title": "Dispute summary",
            "body": (
                f"Chargeback {state['chargeback_id']} for {state['dispute_amount']:.2f} "
                f"{state['currency']} under reason code {state['reason_code']}."
            ),
        },
        {
            "title": "Decision rationale",
            "body": state.get("decision_reasoning") or "Evidence supports representment.",
        },
        {
            "title": "Evidence highlights",
            "body": "; ".join(strongest) if strongest else "No strong evidence signals were available.",
        },
    ]


def _build_rebuttal_packet(state: ChargebackState) -> dict[str, Any]:
    return {
        "chargeback_id": state["chargeback_id"],
        "merchant": state["merchant_profile"]["name"],
        "reason_code": state["reason_code"],
        "card_network": state["card_network"],
        "amount": state["dispute_amount"],
        "currency": state["currency"],
        "win_probability": state.get("win_probability"),
        "expected_value": state.get("expected_value"),
        "decision_reasoning": state.get("decision_reasoning"),
        "evidence_status": _evidence_status(state),
        "strongest_evidence": _strongest_evidence(state),
        "sections": _rebuttal_sections(state),
        "evidence": {
            "transaction": state.get("transaction"),
            "shipping": state.get("shipping"),
            "device": state.get("device"),
            "comms": state.get("comms"),
            "consortium": state.get("consortium"),
            "delivery_photo": state.get("delivery_photo"),
            "order_timeline": state.get("order_timeline"),
        },
    }


def rebuttal_builder_agent(state: ChargebackState) -> ChargebackState:
    """Build a deterministic rebuttal packet for downstream PDF generation/filing."""
    logger.info("Running rebuttal builder agent for %s", state["chargeback_id"])

    output_dir = _output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{state['chargeback_id']}_rebuttal.json"

    packet = _build_rebuttal_packet(state)
    path.write_text(json.dumps(packet, default=str, indent=2), encoding="utf-8")
    state["rebuttal_document_path"] = str(path)
    return state
