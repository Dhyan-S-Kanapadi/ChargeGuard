import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from agents.quality_check import quality_check_agent
from api.store import store
from core.graph import route_quality
from core.state import ChargebackState
from documents.pdf_builder import build_rebuttal_pdf
from integrations.artifact_storage import artifact_object_key, artifact_storage


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


def _write_artifacts(tmp_path, state: ChargebackState, packet: dict):
    pdf_path = tmp_path / "rebuttal.pdf"
    build_rebuttal_pdf(packet, pdf_path, template_text="Factual representment.")
    if store.get_dispute(state["chargeback_id"]) is None:
        assert store.create_dispute(state)
    merchant_id = state["merchant_profile"]["merchant_id"]
    for field, artifact_type, content_type, filename, content in (
        ("rebuttal_artifact_id", "rebuttal_pdf", "application/pdf", "rebuttal.pdf", pdf_path.read_bytes()),
        ("rebuttal_facts_artifact_id", "rebuttal_facts", "application/json", "rebuttal.json", json.dumps(packet).encode()),
    ):
        artifact_id = uuid4().hex
        state[field] = artifact_id
        key = artifact_object_key(merchant_id=merchant_id, chargeback_id=state["chargeback_id"], artifact_id=artifact_id, filename=filename)
        saved = artifact_storage().put_immutable(key, content)
        assert store.create_artifact({"artifact_id": artifact_id, "merchant_id": merchant_id,
                                      "chargeback_id": state["chargeback_id"], "artifact_type": artifact_type,
                                      "object_key": saved.object_key, "content_type": content_type,
                                      "size_bytes": saved.size_bytes, "sha256": saved.sha256})


def test_quality_check_approves_valid_rebuttal_pdf(tmp_path) -> None:
    state = _state()
    _write_artifacts(tmp_path, state, _packet())

    result = quality_check_agent(state)

    assert result["quality_approved"] is True
    assert result["quality_rejection_reason"] is None
    assert result["quality_loop_count"] == 1
    persisted = store.get_dispute(state["chargeback_id"])
    assert persisted is not None
    assert persisted["state"]["quality_approved"] is True
    assert store.get_artifact(state["rebuttal_artifact_id"])["immutable_at"] is not None


def test_quality_approval_rolls_back_finalization_when_dispute_save_fails(tmp_path, monkeypatch) -> None:
    state = _state()
    state["chargeback_id"] = "cb_quality_save_failure"
    packet = _packet()
    packet["chargeback_id"] = state["chargeback_id"]
    _write_artifacts(tmp_path, state, packet)
    monkeypatch.setattr(store, "_save", lambda: (_ for _ in ()).throw(OSError("disk unavailable")))

    with pytest.raises(OSError, match="disk unavailable"):
        quality_check_agent(state)

    assert store.get_artifact(state["rebuttal_artifact_id"])["immutable_at"] is None
    assert store.get_dispute(state["chargeback_id"])["state"]["quality_approved"] is False


def test_quality_check_rejects_missing_required_evidence(tmp_path) -> None:
    packet = _packet()
    packet["evidence_status"]["shipping"] = False
    state = _state()
    _write_artifacts(tmp_path, state, packet)

    result = quality_check_agent(state)

    assert result["quality_approved"] is False
    assert result["quality_rejection_reason"] == "missing_shipping_evidence"
    assert result["quality_rejection_details"] == {"missing_evidence": ["shipping"]}
    assert result["quality_auto_fixable"] is False
    assert route_quality(result) == "escalate"


def test_quality_check_rejects_non_pdf_document(tmp_path) -> None:
    state = _state()
    packet = _packet()
    _write_artifacts(tmp_path, state, packet)
    artifact = store.get_artifact(state["rebuttal_artifact_id"])
    assert artifact
    # Metadata says PDF, but the stored bytes are invalid.
    from pathlib import Path
    Path(artifact_storage().root / artifact["object_key"]).write_bytes(b"not a pdf")

    result = quality_check_agent(state)

    assert result["quality_approved"] is False
    assert result["quality_rejection_reason"] == "artifact_checksum_mismatch"


def test_quality_check_enforces_three_attempt_limit() -> None:
    state = _state()
    state["quality_loop_count"] = 3

    result = quality_check_agent(state)

    assert result["quality_loop_count"] == 3
    assert result["quality_approved"] is False
    assert result["quality_rejection_reason"] == "quality_attempt_limit_reached"
