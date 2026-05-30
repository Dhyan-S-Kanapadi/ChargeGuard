import json
import logging
from pathlib import Path
from typing import Any

from core.state import ChargebackState


logger = logging.getLogger(__name__)


def _required_evidence(state: ChargebackState) -> list[str]:
    required = ["transaction", "shipping"]
    if state.get("requires_food_agents"):
        required.extend(["delivery_photo", "order_timeline"])
    return required


def _load_rebuttal_packet(path: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "Missing rebuttal document."
    except json.JSONDecodeError:
        return None, "Rebuttal document is not valid JSON."


def _packet_rejection_reason(state: ChargebackState, packet: dict[str, Any]) -> str | None:
    sections = packet.get("sections") or []
    if len(sections) < 3:
        return "Rebuttal document is missing required sections."

    evidence_status = packet.get("evidence_status") or {}
    missing = [name for name in _required_evidence(state) if not evidence_status.get(name)]
    if missing:
        return f"Missing required evidence: {', '.join(missing)}."

    if not packet.get("strongest_evidence"):
        return "Rebuttal document has no evidence highlights."

    return None


def quality_check_agent(state: ChargebackState) -> ChargebackState:
    """Validate that the dispute packet has the minimum evidence to file."""
    logger.info("Running quality check agent for %s", state["chargeback_id"])

    state["quality_loop_count"] = state.get("quality_loop_count", 0) + 1

    path = state.get("rebuttal_document_path")
    if not path:
        state["quality_approved"] = False
        state["quality_rejection_reason"] = "Missing rebuttal document."
        return state

    packet, load_error = _load_rebuttal_packet(path)
    if load_error or packet is None:
        state["quality_approved"] = False
        state["quality_rejection_reason"] = load_error
        return state

    rejection_reason = _packet_rejection_reason(state, packet)
    if rejection_reason:
        state["quality_approved"] = False
        state["quality_rejection_reason"] = rejection_reason
        return state

    state["quality_approved"] = True
    state["quality_rejection_reason"] = None
    return state
