import json
from datetime import datetime, timedelta, timezone

from agents.quality_check import quality_check_agent
from core.state import ChargebackState
from documents.pdf_builder import build_rebuttal_pdf


def _state() -> ChargebackState:
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    return {
        "chargeback_id": "cb_quality_001",
        "reason_code": "13.1",
        "card_network": "VISA",
        "dispute_amount": 2500.0,
        "currency": "INR",
        "filing_deadline": now + timedelta(days=30),
        "merchant_profile": {
            "merchant_id": "merchant_001",
            "name": "Demo Merchant",
            "vertical": "ecommerce",
            "razorpay_key": "",
            "shiprocket_key": "",
            "freshdesk_domain": "",
            "average_order_value": 1800.0,
            "chargeback_history_count": 4,
        },
        "investigation_plan": {},
        "requires_food_agents": False,
        "transaction": None,
        "shipping": None,
        "comms": None,
        "device": None,
        "consortium": None,
        "delivery_photo": None,
        "order_timeline": None,
        "win_probability": 0.82,
        "expected_value": 2035.0,
        "decision": "FIGHT",
        "decision_reasoning": "Strong evidence supports representment.",
        "rebuttal_document_path": None,
        "quality_approved": False,
        "quality_rejection_reason": None,
        "quality_loop_count": 0,
        "filing_confirmation": None,
        "filed_at": None,
        "final_outcome": None,
        "outcome_reason": None,
        "outcome_recorded_at": None,
    }


def _packet() -> dict:
    return {
        "chargeback_id": "cb_quality_001",
        "merchant": "Demo Merchant",
        "card_network": "VISA",
        "reason_code": "13.1",
        "amount": 2500.0,
        "currency": "INR",
        "required_evidence": ["transaction", "shipping"],
        "evidence_priority": ["shipping", "transaction"],
        "sections": [
            {"title": "Dispute summary", "body": "summary"},
            {"title": "Decision rationale", "body": "rationale"},
            {"title": "Evidence highlights", "body": "highlights"},
        ],
        "evidence_status": {"transaction": True, "shipping": True},
        "strongest_evidence": ["3DS authentication completed"],
    }


def _write_artifacts(tmp_path, packet: dict):
    pdf_path = tmp_path / "rebuttal.pdf"
    build_rebuttal_pdf(packet, pdf_path, template_text="Factual representment.")
    pdf_path.with_suffix(".json").write_text(
        json.dumps(packet), encoding="utf-8"
    )
    return pdf_path


def test_quality_check_approves_valid_rebuttal_pdf(tmp_path) -> None:
    path = _write_artifacts(tmp_path, _packet())
    state = _state()
    state["rebuttal_document_path"] = str(path)

    result = quality_check_agent(state)

    assert result["quality_approved"] is True
    assert result["quality_rejection_reason"] is None
    assert result["quality_loop_count"] == 1


def test_quality_check_rejects_missing_required_evidence(tmp_path) -> None:
    packet = _packet()
    packet["evidence_status"]["shipping"] = False
    path = _write_artifacts(tmp_path, packet)
    state = _state()
    state["rebuttal_document_path"] = str(path)

    result = quality_check_agent(state)

    assert result["quality_approved"] is False
    assert result["quality_rejection_reason"] == "Missing required evidence: shipping."


def test_quality_check_rejects_non_pdf_document(tmp_path) -> None:
    path = tmp_path / "rebuttal.json"
    path.write_text("{}", encoding="utf-8")
    state = _state()
    state["rebuttal_document_path"] = str(path)

    result = quality_check_agent(state)

    assert result["quality_approved"] is False
    assert result["quality_rejection_reason"] == "Rebuttal document is not a valid PDF."


def test_quality_check_enforces_three_attempt_limit() -> None:
    state = _state()
    state["quality_loop_count"] = 3

    result = quality_check_agent(state)

    assert result["quality_loop_count"] == 3
    assert result["quality_approved"] is False
    assert result["quality_rejection_reason"] == "Quality review attempt limit reached."
