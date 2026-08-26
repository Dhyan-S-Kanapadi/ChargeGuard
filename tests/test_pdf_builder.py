import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agents.rebuttal_builder import _build_rebuttal_packet, rebuttal_builder_agent
from core.state import ChargebackState


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

    result = rebuttal_builder_agent(_state())

    assert result["rebuttal_document_path"] is not None
    path = Path(result["rebuttal_document_path"])
    assert path.exists()
    assert path.suffix == ".pdf"
    assert path.read_bytes().startswith(b"%PDF-")
    packet = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert packet["chargeback_id"] == "cb_rebuttal_001"
    assert packet["sections"][0]["title"] == "Dispute summary"


def test_rebuttal_pdf_is_deterministic(tmp_path, monkeypatch) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    monkeypatch.setenv("REBUTTAL_OUTPUT_DIR", str(first_dir))
    first_path = Path(rebuttal_builder_agent(_state())["rebuttal_document_path"])
    monkeypatch.setenv("REBUTTAL_OUTPUT_DIR", str(second_dir))
    second_path = Path(rebuttal_builder_agent(_state())["rebuttal_document_path"])

    assert first_path.read_bytes() == second_path.read_bytes()
