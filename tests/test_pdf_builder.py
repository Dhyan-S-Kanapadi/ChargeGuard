import json
from datetime import datetime, timedelta, timezone

import pytest

from agents.rebuttal_builder import _build_rebuttal_packet, rebuttal_builder_agent
from agents.quality_check import quality_check_agent
from api.store import store
from core.state import ChargebackState
from integrations.artifact_storage import artifact_storage


def _state() -> ChargebackState:
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    return {
        "chargeback_id": "cb_rebuttal_001",
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
        "transaction": {
            "order_id": "order_demo_001",
            "payment_id": "pay_demo_001",
            "amount": 2500.0,
            "currency": "INR",
            "otp_verified": True,
            "three_ds_authenticated": True,
            "device_id": "device_demo_123",
            "ip_address": "49.36.18.22",
            "customer_email": "buyer@example.com",
            "order_history_count": 8,
            "previous_chargebacks": 0,
            "raw": {},
        },
        "shipping": {
            "tracking_id": "trk_demo_001",
            "courier": "Shiprocket",
            "status": "DELIVERED",
            "delivered_at": now + timedelta(days=2),
            "delivery_latitude": 12.9716,
            "delivery_longitude": 77.5946,
            "signature_obtained": True,
            "delivery_photo_url": "https://example.test/pod/trk_demo_001.jpg",
            "raw": {},
        },
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


def _persist(state: ChargebackState) -> ChargebackState:
    if store.get_dispute(state["chargeback_id"]) is None:
        assert store.create_dispute(state)
    return state


def _artifact_content(state: ChargebackState, artifact_key: str) -> bytes:
    artifact = store.get_artifact(state[artifact_key])
    assert artifact is not None
    return artifact_storage().read_verified(artifact["object_key"], artifact["sha256"])


def test_rebuttal_packet_includes_status_sections_and_evidence() -> None:
    state = _state()
    state["contradiction_flags"] = [
        "claims non-receipt, but delivery confirmed with signature on file",
    ]
    state["contradiction_summary"] = "1 evidence contradiction identified: delivery signature is on file."
    packet = _build_rebuttal_packet(state)

    assert packet["chargeback_id"] == "cb_rebuttal_001"
    assert packet["merchant"] == "Demo Merchant"
    assert packet["evidence_status"]["transaction"] is True
    assert packet["evidence_status"]["shipping"] is True
    assert packet["evidence_status"]["device"] is False
    assert "3DS authentication completed" in packet["strongest_evidence"]
    assert "Shipment marked delivered" in packet["strongest_evidence"]
    assert packet["sections"][-1] == {
        "title": "Evidence contradictions",
        "body": "1 evidence contradiction identified: delivery signature is on file.",
    }
    assert packet["contradiction_flags"] == state["contradiction_flags"]
    assert packet["evidence"]["transaction"] is not None


def test_rebuttal_builder_writes_pdf_and_fact_sidecar(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REBUTTAL_OUTPUT_DIR", str(tmp_path))

    result = rebuttal_builder_agent(_persist(_state()))

    assert result["rebuttal_document_path"] is None
    assert result["rebuttal_artifact_id"]
    assert _artifact_content(result, "rebuttal_artifact_id").startswith(b"%PDF-")
    packet = json.loads(_artifact_content(result, "rebuttal_facts_artifact_id"))
    assert packet["chargeback_id"] == "cb_rebuttal_001"
    assert packet["sections"][0]["title"] == "Dispute summary"
    assert packet["narrative_generated"] is False


def test_rebuttal_builder_requires_persisted_merchant_owned_dispute(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARGEGUARD_ARTIFACT_LOCAL_DIR", str(tmp_path))
    state = _state()
    state["chargeback_id"] = "cb_unpersisted_artifact"

    with pytest.raises(RuntimeError, match="stored merchant-owned dispute"):
        rebuttal_builder_agent(state)

    assert not list(tmp_path.rglob("*"))


def test_rebuttal_builder_cleans_uncommitted_objects_when_metadata_write_fails(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARGEGUARD_ARTIFACT_LOCAL_DIR", str(tmp_path))
    state = _state()
    state["chargeback_id"] = "cb_artifact_metadata_failure"
    state = _persist(state)
    record_before = store.get_dispute(state["chargeback_id"])
    assert record_before is not None

    monkeypatch.setattr(store, "attach_rebuttal_artifacts", lambda *args: None)
    with pytest.raises(RuntimeError, match="metadata could not be persisted"):
        rebuttal_builder_agent(state)

    assert not list(tmp_path.rglob("*.pdf"))
    assert not list(tmp_path.rglob("*.json"))
    record_after = store.get_dispute(state["chargeback_id"])
    assert record_after is not None
    assert record_after["state"].get("rebuttal_artifact_id") is None
    assert record_after["state"].get("rebuttal_facts_artifact_id") is None


def test_enabled_stubbed_narrative_is_first_packet_section(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REBUTTAL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("REBUTTAL_NARRATIVE_ENABLED", "true")
    monkeypatch.setenv("REBUTTAL_NARRATIVE_USE_STUBS", "true")

    result = rebuttal_builder_agent(_persist(_state()))
    packet = json.loads(_artifact_content(result, "rebuttal_facts_artifact_id"))

    assert packet["narrative_generated"] is True
    assert packet["sections"][0]["title"] == "Summary"
    assert "3DS authentication completed" in packet["sections"][0]["body"]


def test_narrative_failure_keeps_valid_deterministic_packet(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REBUTTAL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("REBUTTAL_NARRATIVE_ENABLED", "true")

    def fail_narrative(packet: dict) -> str:
        raise RuntimeError("narrative service unavailable")

    monkeypatch.setattr("agents.rebuttal_builder.generate_rebuttal_narrative", fail_narrative)
    result = rebuttal_builder_agent(_persist(_state()))
    pdf = _artifact_content(result, "rebuttal_artifact_id")
    packet = json.loads(_artifact_content(result, "rebuttal_facts_artifact_id"))

    assert pdf.startswith(b"%PDF-")
    assert packet["narrative_generated"] is False
    assert packet["sections"][0]["title"] == "Dispute summary"


def test_generated_narrative_prohibited_language_is_rejected(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REBUTTAL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("REBUTTAL_NARRATIVE_ENABLED", "true")
    monkeypatch.setattr(
        "agents.rebuttal_builder.generate_rebuttal_narrative",
        lambda packet: "We accept liability because of merchant error.",
    )
    state = _persist(_state())
    state["comms"] = {
        "emails": [],
        "support_tickets": [],
        "post_delivery_interaction": False,
        "complaint_raised_before_chargeback": False,
        "raw": {},
    }

    rebuttal_builder_agent(state)
    result = quality_check_agent(state)

    assert result["quality_rejection_reason"] == "prohibited_language_used"
    assert result["quality_rejection_details"]["phrases"] == [
        "we accept liability",
        "merchant error",
    ]


def test_rebuttal_pdf_is_deterministic(tmp_path, monkeypatch) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    monkeypatch.setenv("REBUTTAL_OUTPUT_DIR", str(first_dir))
    first = rebuttal_builder_agent(_persist(_state()))
    first_pdf = _artifact_content(first, "rebuttal_artifact_id")
    monkeypatch.setenv("REBUTTAL_OUTPUT_DIR", str(second_dir))
    second = rebuttal_builder_agent(_persist(_state()))
    second_pdf = _artifact_content(second, "rebuttal_artifact_id")

    assert first_pdf == second_pdf


def test_rebuttal_retry_removes_prohibited_language(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REBUTTAL_OUTPUT_DIR", str(tmp_path))
    state = _persist(_state())
    state["decision_reasoning"] = "We accept liability due to merchant error."

    rebuttal_builder_agent(state)
    first_packet = _artifact_content(state, "rebuttal_facts_artifact_id").decode("utf-8")
    state["quality_rejection_reason"] = "prohibited_language_used"
    state["quality_loop_count"] = 1
    rebuttal_builder_agent(state)
    second_packet = _artifact_content(state, "rebuttal_facts_artifact_id").decode("utf-8")

    assert second_packet != first_packet
    assert "we accept liability" not in second_packet.lower()
    assert "merchant error" not in second_packet.lower()
    assert '"reason": "prohibited_language_used"' in second_packet
