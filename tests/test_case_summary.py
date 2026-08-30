from datetime import datetime, timedelta, timezone
import json
from typing import cast

import httpx
from fastapi.testclient import TestClient

from agents import escalation
from agents.escalation import human_escalation_agent
from api.store import store
from core.state import ChargebackState
from main import app
from integrations.case_summary import CaseSummaryClient


def _state(decision: str = "ESCALATE_DEGRADED") -> ChargebackState:
    return cast(
        ChargebackState,
        {
            "chargeback_id": "cb_case_summary_001",
            "reason_code": "10.4",
            "card_network": "VISA",
            "dispute_amount": 2500.0,
            "currency": "INR",
            "filing_deadline": datetime.now(timezone.utc) + timedelta(days=30),
            "merchant_profile": {"merchant_id": "merchant_001", "name": "Demo"},
            "transaction": {"otp_verified": True},
            "shipping": {"status": "DELIVERED"},
            "comms": None,
            "device": None,
            "consortium": None,
            "delivery_photo": None,
            "order_timeline": None,
            "win_probability": 0.55,
            "expected_value": 175.0,
            "third_party_fraud_indicators": {"score": 20.0, "label": "low"},
            "identity_continuity": {"score": 80.0, "label": "high"},
            "contradiction_flags": [],
            "evidence_collection_degraded": True,
            "degraded_reasons": ["device"],
            "decision": decision,
            "decision_reasoning": "Evidence collection degraded; human review required.",
            "quality_approved": False,
            "quality_rejection_reason": None,
            "quality_loop_count": 0,
            "rebuttal_document_path": None,
            "filing_confirmation": None,
            "filed_at": None,
            "final_outcome": None,
            "outcome_reason": None,
            "outcome_recorded_at": None,
        },
    )


def test_degraded_escalation_populates_stubbed_summary(monkeypatch) -> None:
    monkeypatch.setenv("CASE_SUMMARY_USE_STUBS", "true")

    result = human_escalation_agent(_state())

    assert result["final_outcome"] == "PENDING"
    assert result["human_review_summary"] is not None
    assert "evidence collection was degraded because of device" in result["human_review_summary"]


def test_model_failure_summary_does_not_blame_evidence_collection(monkeypatch) -> None:
    """A model failure must not be represented as an evidence-collection failure."""
    monkeypatch.setenv("CASE_SUMMARY_USE_STUBS", "true")

    state = _state()
    state["transaction"] = {"otp_verified": True}
    state["shipping"] = {"status": "DELIVERED"}
    state["evidence_collection_degraded"] = False
    state["degraded_reasons"] = []
    state["decision_reasoning"] = (
        "Model unavailable; win probability defaulted to 0.0 (model_unavailable)."
    )

    result = human_escalation_agent(state)

    assert result["human_review_summary"] is not None
    assert "evidence collection" not in result["human_review_summary"].lower()
    assert "model" in result["human_review_summary"].lower()


def test_case_summary_client_uses_a_constrained_summary_tool() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["tool_choice"] == {"type": "tool", "name": "return_case_summary"}
        assert payload["tools"][0]["input_schema"]["required"] == ["summary"]
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "tool_use",
                        "name": "return_case_summary",
                        "input": {"summary": "Evidence is incomplete and needs review."},
                    }
                ]
            },
        )

    client = CaseSummaryClient(
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.summarize_case({"evidence_status": {"transaction": True}}) == (
        "Evidence is incomplete and needs review."
    )


def test_non_degraded_decisions_do_not_generate_automatic_summary(monkeypatch) -> None:
    monkeypatch.setenv("CASE_SUMMARY_USE_STUBS", "true")

    result = human_escalation_agent(_state("FIGHT"))

    assert result["final_outcome"] == "PENDING"
    assert result.get("human_review_summary") is None


def test_summary_failure_does_not_change_escalation(monkeypatch) -> None:
    def fail_summary(state: ChargebackState) -> str:
        raise RuntimeError("summary service unavailable")

    monkeypatch.setattr(escalation, "generate_case_summary", fail_summary)
    state = _state()
    result = human_escalation_agent(state)

    assert result["final_outcome"] == "PENDING"
    assert result["filing_confirmation"] == "human_review_required"
    assert result["filed_at"] is None
    assert result["human_review_summary"] is None


def test_on_demand_summary_supports_fight_cases(monkeypatch) -> None:
    store.clear()
    monkeypatch.setenv("API_KEY", "test-api-key")
    monkeypatch.setenv("CASE_SUMMARY_USE_STUBS", "true")
    state = _state("FIGHT")
    state["evidence_collection_degraded"] = False
    state["degraded_reasons"] = []
    assert store.create_dispute(state)

    response = TestClient(app).get(
        "/disputes/cb_case_summary_001/summary",
        headers={"X-API-Key": "test-api-key"},
    )

    assert response.status_code == 200
    assert response.json()["chargeback_id"] == "cb_case_summary_001"
    assert "Available evidence includes transaction, shipping" in response.json()["human_review_summary"]
    store.clear()
