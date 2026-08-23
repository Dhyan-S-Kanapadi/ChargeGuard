from datetime import datetime, timedelta, timezone
import json

from agents.evidence import device
from core import graph as graph_module
from core.graph import app
from core.state import ChargebackState
from ml.train import train_baseline_model


def test_chargeback_graph_runs_from_start_to_end(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REBUTTAL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "true")
    model_path = tmp_path / "win_probability_model.pkl"
    train_baseline_model(output_path=model_path, count=200, seed=42)
    monkeypatch.setenv("MODEL_PATH", str(model_path))

    now = datetime.now(timezone.utc)
    state: ChargebackState = {
        "chargeback_id": "cb_test_001",
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

    result = app.invoke(state)

    assert result["chargeback_id"] == "cb_test_001"
    assert result["decision"] == "FIGHT"
    assert result["transaction"] is not None
    assert result["shipping"] is not None
    assert result["device"] is not None
    assert result["comms"] is not None
    assert result["consortium"] is not None
    assert result["quality_approved"] is True
    assert result["rebuttal_document_path"] is not None
    assert result["filing_confirmation"] is not None
    assert result["filing_confirmation"].startswith("filed_visa_cb_test_001_")


def test_low_ev_graph_acceptance_does_not_record_training_outcome(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("REBUTTAL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "true")
    model_path = tmp_path / "win_probability_model.pkl"
    train_baseline_model(output_path=model_path, count=200, seed=42)
    monkeypatch.setenv("MODEL_PATH", str(model_path))
    training_path = tmp_path / "outcomes.json"
    training_path.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("TRAINING_DATA_PATH", str(training_path))
    monkeypatch.setenv("TRAINING_METADATA_PATH", str(tmp_path / "metadata.json"))
    monkeypatch.setenv("PLAYBOOK_STATS_PATH", str(tmp_path / "stats.json"))

    now = datetime.now(timezone.utc)
    state: ChargebackState = {
        "chargeback_id": "cb_low_ev_001",
        "reason_code": "10.4",
        "card_network": "VISA",
        "dispute_amount": 10.0,
        "currency": "USD",
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
        "decision": None,
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

    result = app.invoke(state)

    assert result["decision"] == "ACCEPT"
    assert result["filing_confirmation"] == "accepted_no_filing"
    assert result["final_outcome"] == "ACCEPTED_NO_CONTEST"
    assert result["outcome_recorded_at"] is None
    assert json.loads(training_path.read_text(encoding="utf-8")) == []


def test_device_collection_failure_escalates_to_human_review(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REBUTTAL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "true")
    model_path = tmp_path / "win_probability_model.pkl"
    train_baseline_model(output_path=model_path, count=200, seed=42)
    monkeypatch.setenv("MODEL_PATH", str(model_path))

    def fail_seon_collection(state: ChargebackState) -> tuple[dict, str]:
        raise RuntimeError("SEON unavailable")

    monkeypatch.setattr(device, "_collect_device_data", fail_seon_collection)
    now = datetime.now(timezone.utc)
    state: ChargebackState = {
        "chargeback_id": "cb_device_failure_001",
        "reason_code": "10.4",
        "card_network": "VISA",
        "dispute_amount": 2_500.0,
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
        "evidence_collection_degraded": False,
        "degraded_reasons": [],
        "win_probability": None,
        "expected_value": None,
        "decision": None,
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

    result = app.invoke(state)

    assert result["device"] is None
    assert result["evidence_collection_degraded"] is True
    assert result["degraded_reasons"] == ["device"]
    assert result["decision"] == "ESCALATE_DEGRADED"
    assert result["filing_confirmation"] == "human_review_required"
    assert result["final_outcome"] == "PENDING"


def test_overdue_case_skips_slow_evidence_and_scores_with_partial_evidence(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("REBUTTAL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "true")
    model_path = tmp_path / "win_probability_model.pkl"
    train_baseline_model(output_path=model_path, count=200, seed=42)
    monkeypatch.setenv("MODEL_PATH", str(model_path))
    slow_agent_calls: list[str] = []

    def unexpected_evidence_agent(name: str):
        def agent(state: ChargebackState) -> ChargebackState:
            slow_agent_calls.append(name)
            raise AssertionError(f"{name} agent must not run for overdue cases")

        return agent

    monkeypatch.setattr(graph_module, "device_agent", unexpected_evidence_agent("device"))
    monkeypatch.setattr(graph_module, "comms_agent", unexpected_evidence_agent("comms"))
    monkeypatch.setattr(graph_module, "consortium_agent", unexpected_evidence_agent("consortium"))
    expedited_app = graph_module.build_graph().compile()
    now = datetime.now(timezone.utc)
    state: ChargebackState = {
        "chargeback_id": "cb_overdue_001",
        "reason_code": "10.4",
        "card_network": "VISA",
        "dispute_amount": 2_500.0,
        "currency": "INR",
        "filing_deadline": now - timedelta(days=1),
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
        "evidence_collection_degraded": False,
        "degraded_reasons": [],
        "win_probability": None,
        "expected_value": None,
        "decision": None,
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

    result = expedited_app.invoke(state)

    assert slow_agent_calls == []
    assert result["transaction"] is not None
    assert result["shipping"] is not None
    assert result["device"] is None
    assert result["comms"] is None
    assert result["consortium"] is None
    assert result["decision"] in {"FIGHT", "ACCEPT"}
    assert "Expedited partial-evidence decision" in result["decision_reasoning"]
