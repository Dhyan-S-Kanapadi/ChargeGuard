from datetime import datetime, timedelta, timezone

from agents.evidence import consortium
from agents.evidence.consortium import _build_consortium_evidence, consortium_agent
from core.state import ChargebackState


def _state() -> ChargebackState:
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    return {
        "chargeback_id": "cb_consortium_001",
        "order_id": "order_demo_001",
        "payment_id": "pay_demo_001",
        "reason_code": "10.4",
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
        "win_probability": None,
        "expected_value": None,
        "decision": "ACCEPT",
        "decision_reasoning": None,
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


def test_consortium_agent_populates_only_consortium_evidence(monkeypatch) -> None:
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "true")
    state = _state()
    result = consortium_agent(state)

    assert result["consortium"] is not None
    assert result["consortium"]["lookup_complete"] is True
    assert result["consortium"]["ethoca_match"] is False
    assert result["consortium"]["verifi_match"] is False
    assert result["consortium"]["cross_merchant_fraud_history"] is False
    assert result["consortium"]["dispute_count_across_merchants"] == 0
    assert result["transaction"] is None
    assert result["device"] is None


def test_consortium_builder_detects_cross_merchant_risk() -> None:
    response = {
        "ethoca": {
            "match": True,
        },
        "verifi": {
            "match": False,
        },
        "history": {
            "dispute_count_across_merchants": 3,
        },
    }

    evidence = _build_consortium_evidence(response)

    assert evidence["ethoca_match"] is True
    assert evidence["verifi_match"] is False
    assert evidence["cross_merchant_fraud_history"] is True
    assert evidence["dispute_count_across_merchants"] == 3


def test_consortium_agent_records_empty_evidence_on_collection_failure(monkeypatch) -> None:
    def fail_consortium_collection(state: ChargebackState) -> dict:
        raise RuntimeError("network intelligence unavailable")

    monkeypatch.setattr(consortium, "_collect_consortium_data", fail_consortium_collection)

    state = _state()
    result = consortium_agent(state)

    assert result["consortium"] is not None
    assert result["consortium"]["lookup_complete"] is False
    assert result["consortium"]["ethoca_match"] is False
    assert result["consortium"]["verifi_match"] is False
    assert result["consortium"]["raw"]["source"] == "consortium_agent_empty"
    assert result["consortium"]["raw"]["error"] == "network intelligence unavailable"


def test_consortium_agent_keeps_ethoca_match_when_verifi_fails(monkeypatch) -> None:
    class FakeEthocaClient:
        def search_alerts(self, identifiers: dict[str, str]) -> dict:
            assert identifiers["payment_id"] == "pay_demo_001"
            return {"alerts": [{"id": "alert_001"}], "dispute_count": 2}

    monkeypatch.delenv("CHARGEGUARD_USE_STUBS", raising=False)
    monkeypatch.setattr(
        consortium.EthocaClient,
        "from_env",
        classmethod(lambda cls: FakeEthocaClient()),
    )
    monkeypatch.setattr(
        consortium.VerifiClient,
        "from_env",
        classmethod(lambda cls: (_ for _ in ()).throw(RuntimeError("Verifi unavailable"))),
    )

    result = consortium_agent(_state())

    assert result["consortium"] is not None
    assert result["consortium"]["lookup_complete"] is False
    assert result["consortium"]["ethoca_match"] is True
    assert result["consortium"]["verifi_match"] is False
    assert result["consortium"]["cross_merchant_fraud_history"] is True
    assert result["consortium"]["raw"]["response"]["source_errors"] == {
        "verifi": "Verifi unavailable"
    }
