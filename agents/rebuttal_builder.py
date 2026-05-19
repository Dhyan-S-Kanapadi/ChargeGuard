import json
import logging
import os
from pathlib import Path

from core.state import ChargebackState


logger = logging.getLogger(__name__)


def _output_dir() -> Path:
    return Path(os.getenv("REBUTTAL_OUTPUT_DIR", "./output/rebuttals"))


def rebuttal_builder_agent(state: ChargebackState) -> ChargebackState:
    """Build a deterministic rebuttal packet for downstream PDF generation/filing."""
    logger.info("Running rebuttal builder agent for %s", state["chargeback_id"])

    output_dir = _output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{state['chargeback_id']}_rebuttal.json"

    packet = {
        "chargeback_id": state["chargeback_id"],
        "merchant": state["merchant_profile"]["name"],
        "reason_code": state["reason_code"],
        "card_network": state["card_network"],
        "amount": state["dispute_amount"],
        "currency": state["currency"],
        "decision_reasoning": state.get("decision_reasoning"),
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

    path.write_text(json.dumps(packet, default=str, indent=2), encoding="utf-8")
    state["rebuttal_document_path"] = str(path)
    return state
