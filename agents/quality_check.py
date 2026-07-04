import json
import logging
import re
from pathlib import Path
from typing import Any

from core.state import ChargebackState


logger = logging.getLogger(__name__)


def _sidecar_path(pdf_path: Path) -> Path:
    return pdf_path.with_suffix(".json")


def _load_rebuttal_packet(pdf_path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return json.loads(_sidecar_path(pdf_path).read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "Missing rebuttal fact sidecar."
    except json.JSONDecodeError:
        return None, "Rebuttal fact sidecar is not valid JSON."


def _pdf_page_count(content: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page\b", content))


def _packet_rejection_reason(state: ChargebackState, packet: dict[str, Any]) -> str | None:
    if packet.get("card_network") != state["card_network"]:
        return "Card network does not match the dispute."
    if packet.get("reason_code") != state["reason_code"]:
        return "Reason code does not match the dispute."
    if packet.get("chargeback_id") != state["chargeback_id"]:
        return "Chargeback ID does not match the dispute."

    sections = packet.get("sections") or []
    if len(sections) < 3:
        return "Rebuttal document is missing required sections."

    evidence_status = packet.get("evidence_status") or {}
    required = packet.get("required_evidence") or []
    missing = [name for name in required if not evidence_status.get(name)]
    if missing:
        return f"Missing required evidence: {', '.join(missing)}."

    if not packet.get("strongest_evidence"):
        return "Rebuttal document has no evidence highlights."

    section_text = " ".join(str(section.get("body", "")) for section in sections).lower()
    prohibited = ("we accept liability", "merchant error", "we were at fault")
    if any(phrase in section_text for phrase in prohibited):
        return "Rebuttal contains prohibited admission language."
    return None


def quality_check_agent(state: ChargebackState) -> ChargebackState:
    """Validate the PDF filing artifact and its structured fact sidecar."""
    logger.info("Running quality check agent for %s", state["chargeback_id"])

    if state.get("quality_loop_count", 0) >= 3:
        state["quality_approved"] = False
        state["quality_rejection_reason"] = "Quality review attempt limit reached."
        return state
    state["quality_loop_count"] = state.get("quality_loop_count", 0) + 1

    path_value = state.get("rebuttal_document_path")
    if not path_value:
        state["quality_approved"] = False
        state["quality_rejection_reason"] = "Missing rebuttal document."
        return state

    pdf_path = Path(path_value)
    try:
        content = pdf_path.read_bytes()
    except FileNotFoundError:
        state["quality_approved"] = False
        state["quality_rejection_reason"] = "Missing rebuttal document."
        return state
    if pdf_path.suffix.lower() != ".pdf" or not content.startswith(b"%PDF-"):
        state["quality_approved"] = False
        state["quality_rejection_reason"] = "Rebuttal document is not a valid PDF."
        return state

    page_limit = 15 if state["card_network"] == "MASTERCARD" else 10
    if _pdf_page_count(content) > page_limit:
        state["quality_approved"] = False
        state["quality_rejection_reason"] = f"Rebuttal exceeds the {page_limit}-page limit."
        return state

    packet, load_error = _load_rebuttal_packet(pdf_path)
    if load_error or packet is None:
        state["quality_approved"] = False
        state["quality_rejection_reason"] = load_error
        return state

    rejection_reason = _packet_rejection_reason(state, packet)
    state["quality_approved"] = rejection_reason is None
    state["quality_rejection_reason"] = rejection_reason
    return state
