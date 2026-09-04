import json
from datetime import datetime, timedelta, timezone
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient

from agents import decision_review as decision_review_agent_module
from agents.decision_review import decision_review_agent
from api.store import store
from core import graph as graph_module
from core.state import ChargebackState
from integrations.decision_review import (
    DecisionReviewClient,
    DecisionReviewRequestError,
    decision_review_facts,
)
from main import app


def _state(decision: str = "FIGHT") -> ChargebackState:
    return cast(
        ChargebackState,
        {
            "chargeback_id": "cb_review_1",
            "reason_code": "10.4",
            "network_reason_code": "10.4",
            "card_network": "VISA",
            "dispute_amount": 2500.0,
            "currency": "INR",
            "filing_deadline": datetime.now(timezone.utc) + timedelta(days=30),
            "merchant_profile": {
                "merchant_id": "merchant_review",
                "name": "Review Merchant",
                "vertical": "ecommerce",
                "freshdesk_domain": "",
                "average_order_value": 1000.0,
                "chargeback_history_count": 0,
            },
            "investigation_plan": {"priority": "normal"},
            "requires_food_agents": False,
            "transaction": {
                "amount": 2500.0,
                "currency": "INR",
                "otp_verified": True,
                "three_ds_authenticated": True,
                "customer_email": "private@example.test",
                "ip_address": "203.0.113.10",
                "device_id": "private-device",
                "shipping_address": "Private address",
            },
            "shipping": {
                "status_category": "DELIVERED",
                "signature_obtained": True,
                "tracking_id": "private-tracking",
            },
            "comms": {
                "emails": [{"body": "private raw message"}],
                "support_tickets": [],
                "post_delivery_interaction": True,
                "complaint_raised_before_chargeback": False,
            },
            "device": {
                "fraud_score": 12.0,
                "device_fingerprint": "private-fingerprint",
                "geolocation_match": True,
                "login_pattern_normal": True,
                "vpn_detected": False,
            },
            "consortium": {
                "lookup_complete": True,
                "ethoca_match": False,
                "verifi_match": False,
                "cross_merchant_fraud_history": False,
            },
            "delivery_photo": None,
            "order_timeline": None,
            "evidence_collection_degraded": decision == "ESCALATE_DEGRADED",
            "degraded_reasons": ["device_provider_unavailable"]
            if decision == "ESCALATE_DEGRADED"
            else [],
            "contradiction_flags": ["OTP authentication contradicts the claim"],
            "win_probability": 0.82,
            "expected_value": 1950.0,
            "decision": decision,
            "decision_reasoning": "Deterministic result.",
            "quality_approved": False,
            "filing_confirmation": None,
            "filed_at": None,
            "final_outcome": None,
            "outcome_reason": None,
            "outcome_recorded_at": None,
            "quality_loop_count": 0,
        },
    )


def _result_json(*, extra: dict | None = None, recommendation: str = "FIGHT") -> str:
    result = {
        "recommendation": recommendation,
        "confidence": 0.86,
        "summary": "Authenticated payment and delivery evidence support the recommendation.",
        "supporting_factors": ["OTP authentication is present", "Delivery is confirmed"],
        "opposing_factors": [],
        "missing_evidence": ["No qualifying historical transactions"],
        "risk_flags": [],
    }
    result.update(extra or {})
    return json.dumps(result)


def _client(handler) -> DecisionReviewClient:
    return DecisionReviewClient(
        base_url="http://127.0.0.1:11434/v1/",
        api_key="secret-test-key",
        model="open-weight-test",
        timeout=2,
        max_tokens=500,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _enable_live(monkeypatch, client: DecisionReviewClient) -> None:
    monkeypatch.setenv("LLM_DECISION_REVIEW_ENABLED", "true")
    monkeypatch.setenv("LLM_DECISION_REVIEW_USE_STUBS", "false")
    monkeypatch.setattr(
        decision_review_agent_module.DecisionReviewClient,
        "from_env",
        classmethod(lambda cls: client),
    )


def test_disabled_mode_makes_no_external_request(monkeypatch) -> None:
    monkeypatch.setenv("LLM_DECISION_REVIEW_ENABLED", "false")
    monkeypatch.setattr(
        decision_review_agent_module.DecisionReviewClient,
        "from_env",
        classmethod(lambda cls: pytest.fail("disabled mode attempted a request")),
    )

    result = decision_review_agent(_state())

    assert result["llm_decision_review"]["status"] == "disabled"
    assert result["decision"] == "FIGHT"


def test_valid_structured_output_is_stored_and_disagreement_is_advisory(monkeypatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": _result_json(recommendation="FIGHT")}}]},
        )

    state = _state("ACCEPT")
    _enable_live(monkeypatch, _client(handler))

    result = decision_review_agent(state)

    assert result["decision"] == "ACCEPT"
    assert result["win_probability"] == 0.82
    assert result["expected_value"] == 1950.0
    assert result["llm_decision_review"]["status"] == "completed"
    assert result["llm_decision_review"]["recommendation"] == "FIGHT"
    assert result["llm_decision_review"]["agreement_with_engine"] is False
    assert result["llm_decision_review"]["model"] == "open-weight-test"


@pytest.mark.parametrize(
    ("content", "expected_requests"),
    [
        ("not-json", 2),
        (_result_json(extra={"decision": "ACCEPT"}), 2),
    ],
)
def test_invalid_or_unexpected_output_fails_safely(
    monkeypatch, content: str, expected_requests: int
) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    _enable_live(monkeypatch, _client(handler))
    result = decision_review_agent(_state())

    assert calls == expected_requests
    assert result["decision"] == "FIGHT"
    assert result["llm_decision_review"]["status"] == "unavailable"
    assert result["llm_decision_review"]["error_code"] == "invalid_response"


def test_timeout_fails_safely(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private provider details", request=request)

    _enable_live(monkeypatch, _client(handler))
    result = decision_review_agent(_state())

    assert result["decision"] == "FIGHT"
    assert result["llm_decision_review"]["error_code"] == "timeout"


@pytest.mark.parametrize(
    ("status_code", "error_code"),
    [
        (401, "authentication_failed"),
        (403, "authentication_failed"),
        (429, "rate_limited"),
        (500, "provider_unavailable"),
    ],
)
def test_http_failures_are_safe_and_do_not_leak_credentials(
    monkeypatch, status_code: int, error_code: str
) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status_code, text="provider body containing secret-test-key")

    _enable_live(monkeypatch, _client(handler))
    result = decision_review_agent(_state())
    serialized = json.dumps(result["llm_decision_review"], default=str)

    assert result["decision"] == "FIGHT"
    assert result["llm_decision_review"]["error_code"] == error_code
    assert calls == 1
    assert "secret-test-key" not in serialized


def test_local_ollama_request_does_not_require_authorization_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://127.0.0.1:11434/v1/chat/completions"
        assert "Authorization" not in request.headers
        payload = json.loads(request.content)
        assert payload["temperature"] == 0
        assert payload["max_tokens"] == 500
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": _result_json()}}]},
        )

    client = DecisionReviewClient(
        base_url="http://127.0.0.1:11434/v1/",
        model="local-model",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.review(decision_review_facts(_state())).recommendation == "FIGHT"


def test_prompt_uses_only_allowlisted_normalized_facts() -> None:
    facts = decision_review_facts(_state())
    serialized = json.dumps(facts)

    for private_value in (
        "private@example.test",
        "203.0.113.10",
        "Private address",
        "private-device",
        "private-fingerprint",
        "private raw message",
        "private-tracking",
    ):
        assert private_value not in serialized
    assert facts["authentication"]["otp_verified"] is True
    assert facts["shipping"]["delivered"] is True


@pytest.mark.parametrize(
    ("decision", "review_behavior", "expected_route"),
    [
        ("ACCEPT", "disagree", "accept"),
        ("FIGHT", "fail", "fight"),
        ("ESCALATE_DEGRADED", "disagree", "escalate"),
    ],
)
def test_graph_routing_remains_deterministic(
    monkeypatch, decision: str, review_behavior: str, expected_route: str
) -> None:
    def identity(state: ChargebackState) -> ChargebackState:
        return state

    def score(state: ChargebackState) -> ChargebackState:
        state["decision"] = decision
        return state

    def review(state: ChargebackState) -> ChargebackState:
        state["llm_decision_review"] = {
            "status": "unavailable" if review_behavior == "fail" else "completed",
            "recommendation": None if review_behavior == "fail" else "FIGHT",
        }
        return state

    def routed(name: str):
        def agent(state: ChargebackState) -> ChargebackState:
            state["decision_reasoning"] = name
            if name == "fight":
                state["quality_approved"] = True
            return state

        return agent

    for name in (
        "orchestrator_agent",
        "transaction_agent",
        "order_correlation_agent",
        "shipping_agent",
        "device_agent",
        "comms_agent",
        "consortium_agent",
        "delivery_photo_agent",
        "order_timeline_agent",
        "purchase_history_agent",
        "quality_check_agent",
        "filing_agent",
    ):
        monkeypatch.setattr(graph_module, name, identity)
    monkeypatch.setattr(graph_module, "scoring_agent", score)
    monkeypatch.setattr(graph_module, "decision_review_agent", review)
    monkeypatch.setattr(graph_module, "accept_and_log_agent", routed("accept"))
    monkeypatch.setattr(graph_module, "rebuttal_builder_agent", routed("fight"))
    monkeypatch.setattr(graph_module, "human_escalation_agent", routed("escalate"))
    result = graph_module.build_graph().compile().invoke(_state(decision))

    assert result["decision"] == decision
    assert result["decision_reasoning"] == expected_route


def test_default_api_response_exposes_only_safe_review_fields(monkeypatch) -> None:
    store.clear()
    monkeypatch.setenv("API_KEY", "test-api-key")
    state = _state()
    state["llm_decision_review"] = {
        "status": "completed",
        "recommendation": "FIGHT",
        "confidence": 0.8,
        "summary": "Safe summary",
        "supporting_factors": [],
        "opposing_factors": [],
        "missing_evidence": [],
        "risk_flags": [],
        "agreement_with_engine": True,
        "model": "safe-model",
        "generated_at": datetime.now(timezone.utc),
        "error_code": None,
        "raw_prompt": "must not escape",
        "api_key": "must not escape",
    }
    assert store.create_dispute(state)

    response = TestClient(app).get(
        "/disputes/cb_review_1", headers={"X-API-Key": "test-api-key"}
    )

    assert response.status_code == 200
    review = response.json()["state"]["llm_decision_review"]
    assert review["summary"] == "Safe summary"
    assert "raw_prompt" not in review
    assert "api_key" not in review
    store.clear()
