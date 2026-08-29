import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from core.state import ChargebackState
from documents.pdf_builder import build_rebuttal_pdf


logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]

_PROHIBITED_REPLACEMENTS = {
    "we accept liability": "the evidence supports representment",
    "merchant error": "documented transaction evidence",
    "we were at fault": "the merchant disputes the claim",
}


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
    if (
        consortium
        and consortium.get("lookup_complete")
        and not consortium["cross_merchant_fraud_history"]
    ):
        evidence.append("No cross-merchant fraud history")
    if delivery_photo and delivery_photo["ai_verified"]:
        evidence.append("Delivery photo verified")
    if timeline and timeline["delivered_at"]:
        evidence.append("Order timeline confirms delivery")

    return evidence


def _rebuttal_sections(state: ChargebackState) -> list[dict[str, str]]:
    strongest = _strongest_evidence(state)
    sections = [
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
    if state.get("contradiction_flags"):
        sections.append(
            {
                "title": "Evidence contradictions",
                "body": state.get("contradiction_summary")
                or "; ".join(state["contradiction_flags"]),
            }
        )
    return sections


def _playbook_path(state: ChargebackState) -> Path:
    network = state["card_network"].lower()
    filename = {
        "visa": "visa_playbooks.json",
        "mastercard": "mastercard_playbooks.json",
        "rupay": "rupay_playbooks.json",
    }.get(network)
    if filename is None:
        raise ValueError(f"No playbook namespace for card network {state['card_network']}")
    return PROJECT_ROOT / "documents" / "playbooks" / filename


def _template_network(state: ChargebackState) -> str:
    network = state["card_network"].lower()
    if network not in {"visa", "mastercard", "rupay"}:
        raise ValueError(f"No rebuttal template namespace for card network {state['card_network']}")
    return network


def _load_playbook(state: ChargebackState) -> dict[str, Any]:
    playbooks = json.loads(_playbook_path(state).read_text(encoding="utf-8"))
    try:
        return playbooks[state["reason_code"]]
    except KeyError as exc:
        raise ValueError(
            f"No {state['card_network']} playbook for reason code {state['reason_code']}"
        ) from exc


def _load_template(state: ChargebackState) -> str:
    network = _template_network(state)
    path = (
        PROJECT_ROOT
        / "documents"
        / "templates"
        / network
        / f"{state['reason_code']}.md"
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    return " ".join(line.strip() for line in lines if line and not line.startswith("#"))


def _build_rebuttal_packet(state: ChargebackState) -> dict[str, Any]:
    playbook = _load_playbook(state)
    packet = {
        "chargeback_id": state["chargeback_id"],
        "merchant": state["merchant_profile"]["name"],
        "reason_code": state["reason_code"],
        "reason_name": playbook["name"],
        "card_network": state["card_network"],
        "amount": state["dispute_amount"],
        "currency": state["currency"],
        "win_probability": state.get("win_probability"),
        "expected_value": state.get("expected_value"),
        "third_party_fraud_indicators": state.get("third_party_fraud_indicators"),
        "identity_continuity": state.get("identity_continuity"),
        "contradiction_flags": state.get("contradiction_flags", []),
        "contradiction_summary": state.get("contradiction_summary"),
        "decision_reasoning": state.get("decision_reasoning"),
        "evidence_status": _evidence_status(state),
        "required_evidence": playbook["required_evidence"],
        "evidence_priority": playbook["evidence_priority"],
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
    return _apply_quality_retry(packet, state)


def _replace_prohibited_language(text: str) -> str:
    sanitized = text
    for phrase, replacement in _PROHIBITED_REPLACEMENTS.items():
        sanitized = re.sub(re.escape(phrase), replacement, sanitized, flags=re.IGNORECASE)
    return sanitized


def _apply_quality_retry(packet: dict[str, Any], state: ChargebackState) -> dict[str, Any]:
    reason = state.get("quality_rejection_reason")
    if not reason:
        return packet

    packet["quality_retry"] = {
        "reason": reason,
        "attempt": state.get("quality_loop_count", 0) + 1,
    }
    if reason == "prohibited_language_used":
        for section in packet["sections"]:
            section["body"] = _replace_prohibited_language(section["body"])
        packet["decision_reasoning"] = _replace_prohibited_language(
            packet.get("decision_reasoning") or ""
        )
    elif reason == "exceeds_page_limit":
        for section in packet["sections"]:
            section["body"] = section["body"][:500]
        packet["strongest_evidence"] = packet["strongest_evidence"][:5]
    return packet


def rebuttal_builder_agent(state: ChargebackState) -> ChargebackState:
    """Build a deterministic PDF and structured fact sidecar."""
    logger.info("Running rebuttal builder agent for %s", state["chargeback_id"])

    output_dir = _output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / f"{state['chargeback_id']}_rebuttal.pdf"
    packet_path = pdf_path.with_suffix(".json")

    packet = _build_rebuttal_packet(state)
    packet_path.write_text(
        json.dumps(packet, default=str, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    template_text = _load_template(state)
    if state.get("quality_rejection_reason") == "prohibited_language_used":
        template_text = _replace_prohibited_language(template_text)
    elif state.get("quality_rejection_reason") == "exceeds_page_limit":
        template_text = template_text[:750]
    build_rebuttal_pdf(packet, pdf_path, template_text=template_text)
    state["rebuttal_document_path"] = str(pdf_path)
    return state
