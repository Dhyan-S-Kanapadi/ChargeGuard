import json
import logging
import re
from pathlib import Path
from typing import Any

from core.state import ChargebackState


logger = logging.getLogger(__name__)


def _sidecar_path(pdf_path: Path) -> Path:
    return pdf_path.with_suffix(".json")


QualityRejection = tuple[str, dict[str, Any], bool]


def _load_rebuttal_packet(pdf_path: Path) -> tuple[dict[str, Any] | None, QualityRejection | None]:
    try:
        return json.loads(_sidecar_path(pdf_path).read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, ("missing_rebuttal_sidecar", {}, True)
    except json.JSONDecodeError:
        return None, ("invalid_rebuttal_sidecar", {}, True)


def _pdf_page_count(content: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page\b", content))


def _packet_rejection_reason(
    state: ChargebackState,
    packet: dict[str, Any],
) -> QualityRejection | None:
    if packet.get("card_network") != state["card_network"]:
        return "card_network_mismatch", {}, True
    if packet.get("reason_code") != state["reason_code"]:
        return "reason_code_mismatch", {}, True
    if packet.get("chargeback_id") != state["chargeback_id"]:
        return "chargeback_id_mismatch", {}, True

    sections = packet.get("sections") or []
    if len(sections) < 3:
        return "missing_required_sections", {"section_count": len(sections)}, True

    evidence_status = packet.get("evidence_status") or {}
    required = packet.get("required_evidence") or []
    missing = [name for name in required if not evidence_status.get(name)]
    if missing:
        reason = f"missing_{missing[0]}_evidence" if len(missing) == 1 else "missing_required_evidence"
        return reason, {"missing_evidence": missing}, False

    if not packet.get("strongest_evidence"):
        return "missing_evidence_highlights", {}, False

    section_text = " ".join(str(section.get("body", "")) for section in sections).lower()
    prohibited = ("we accept liability", "merchant error", "we were at fault")
    matched_phrases = [phrase for phrase in prohibited if phrase in section_text]
    if matched_phrases:
        return "prohibited_language_used", {"phrases": matched_phrases}, True
    return None


def _reject(
    state: ChargebackState,
    rejection: QualityRejection,
) -> ChargebackState:
    reason, details, auto_fixable = rejection
    state["quality_approved"] = False
    state["quality_rejection_reason"] = reason
    state["quality_rejection_details"] = details
    state["quality_auto_fixable"] = auto_fixable
    return state


def quality_check_agent(state: ChargebackState) -> ChargebackState:
    """Validate the PDF filing artifact and its structured fact sidecar."""
    logger.info("Running quality check agent for %s", state["chargeback_id"])

    if state.get("quality_loop_count", 0) >= 3:
        return _reject(state, ("quality_attempt_limit_reached", {}, False))
    state["quality_loop_count"] = state.get("quality_loop_count", 0) + 1

    path_value = state.get("rebuttal_document_path")
    if not path_value:
        return _reject(state, ("missing_rebuttal_document", {}, True))

    pdf_path = Path(path_value)
    try:
        content = pdf_path.read_bytes()
    except FileNotFoundError:
        return _reject(state, ("missing_rebuttal_document", {}, True))
    if pdf_path.suffix.lower() != ".pdf" or not content.startswith(b"%PDF-"):
        return _reject(state, ("invalid_rebuttal_document", {}, True))

    page_limit = 15 if state["card_network"] == "MASTERCARD" else 10
    if _pdf_page_count(content) > page_limit:
        return _reject(
            state,
            ("exceeds_page_limit", {"page_limit": page_limit}, True),
        )

    packet, load_rejection = _load_rebuttal_packet(pdf_path)
    if load_rejection or packet is None:
        return _reject(
            state,
            load_rejection or ("invalid_rebuttal_sidecar", {}, True),
        )

    rejection = _packet_rejection_reason(state, packet)
    if rejection:
        return _reject(state, rejection)
    state["quality_approved"] = True
    state["quality_rejection_reason"] = None
    state["quality_rejection_details"] = {}
    state["quality_auto_fixable"] = True
    return state
