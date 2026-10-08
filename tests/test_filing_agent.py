from datetime import datetime, timedelta, timezone
from uuid import uuid4

from agents.filing import filing_agent
from api.store import store
from core.state import ChargebackState
from integrations.artifact_storage import artifact_object_key, artifact_storage


def _state() -> ChargebackState:
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    return {
        "chargeback_id": "cb_filing_001",
        "order_id": "order_demo_001",
        "payment_id": "pay_demo_001",
        "tracking_id": "trk_demo_001",
        "reason_code": "13.1",
        "card_network": "VISA",
        "dispute_amount": 2500.0,
        "currency": "INR",
        "filing_deadline": now + timedelta(days=30),
        "merchant_profile": {
            "merchant_id": "merchant_001",
            "name": "Demo Merchant",
            "vertical": "ecommerce",
            "razorpay_key": "rzp_test_demo",
            "shiprocket_key": "shiprocket_demo",
            "freshdesk_domain": "demo.freshdesk.com",
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


def _approved_artifact(state: ChargebackState) -> None:
    if store.get_dispute(state["chargeback_id"]) is None:
        assert store.create_dispute(state)
    artifact_id = uuid4().hex
    merchant_id = state["merchant_profile"]["merchant_id"]
    key = artifact_object_key(merchant_id=merchant_id, chargeback_id=state["chargeback_id"], artifact_id=artifact_id, filename="rebuttal.pdf")
    saved = artifact_storage().put_immutable(key, b"%PDF- artifact")
    assert store.create_artifact({"artifact_id": artifact_id, "merchant_id": merchant_id,
                                  "chargeback_id": state["chargeback_id"], "artifact_type": "rebuttal_pdf",
                                  "object_key": saved.object_key, "content_type": "application/pdf",
                                  "size_bytes": saved.size_bytes, "sha256": saved.sha256})
    assert store.finalize_artifacts(merchant_id, state["chargeback_id"], [artifact_id])
    state["rebuttal_artifact_id"] = artifact_id


def test_filing_agent_records_confirmation_for_approved_packet(tmp_path) -> None:
    state = _state()
    state["quality_approved"] = True
    _approved_artifact(state)

    result = filing_agent(state)

    assert result["filed_at"] is not None
    assert result["filing_confirmation"] is not None
    assert result["filing_confirmation"].startswith("filed_visa_cb_filing_001_")


def test_filing_agent_blocks_unapproved_packets(tmp_path) -> None:
    state = _state()
    state["quality_approved"] = False
    _approved_artifact(state)

    result = filing_agent(state)

    assert result["filed_at"] is None
    assert result["filing_confirmation"] == "filing_blocked_quality_not_approved"


def test_filing_agent_blocks_missing_rebuttal_document() -> None:
    state = _state()
    state["quality_approved"] = True

    result = filing_agent(state)

    assert result["filed_at"] is None
    assert result["filing_confirmation"] == "filing_blocked_missing_rebuttal"


def test_filing_agent_rechecks_approved_artifact_integrity() -> None:
    state = _state()
    state["quality_approved"] = True
    _approved_artifact(state)
    artifact = store.get_artifact(state["rebuttal_artifact_id"])
    assert artifact is not None
    from pathlib import Path
    Path(artifact_storage().root / artifact["object_key"]).write_bytes(b"tampered")

    result = filing_agent(state)

    assert result["filed_at"] is None
    assert result["filing_confirmation"] == "filing_blocked_missing_rebuttal"
