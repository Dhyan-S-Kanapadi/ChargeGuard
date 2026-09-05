import json
from datetime import datetime, timezone
from typing import cast

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api import assistant
from api.assistant import (
    build_assistant_context,
    enforce_assistant_rate_limit,
    reset_assistant_rate_limiter,
)
from api.store import store
from core.state import ChargebackState
from main import app
from integrations.portfolio_assistant import PortfolioAssistantClient


@pytest.fixture(autouse=True)
def clear_state() -> None:
    store.clear()
    reset_assistant_rate_limiter()
    yield
    store.clear()
    reset_assistant_rate_limiter()


def _state(chargeback_id: str, decision: str = "FIGHT") -> ChargebackState:
    return cast(
        ChargebackState,
        {
            "chargeback_id": chargeback_id,
            "decision": decision,
            "win_probability": 0.8,
            "expected_value": 120.0,
            "final_outcome": "WIN",
            "contradiction_summary": "Delivery signature is on file.",
            "decision_reasoning": "Expected recovery exceeds response cost.",
            "quality_approved": True,
            "filed_at": datetime.now(timezone.utc),
            "filing_confirmation": "filed_visa_receipt",
            "evidence_collection_degraded": False,
            "transaction": {
                "customer_email": "buyer@example.test",
                "ip_address": "192.0.2.10",
                "device_id": "device-secret",
                "raw": {"provider_secret": "must-not-reach-prompt"},
            },
        },
    )


def test_assistant_endpoint_receives_bounded_grounded_context(monkeypatch) -> None:
    monkeypatch.setenv("API_KEY", "test-api-key")
    assert store.create_dispute(_state("cb_assistant_001"))
    received: dict = {}

    def fake_answer(question: str, context: dict) -> str:
        received["question"] = question
        received["context"] = context
        return "The portfolio contains the supplied dispute."

    monkeypatch.setattr(assistant, "generate_portfolio_answer", fake_answer)
    response = TestClient(app).post(
        "/assistant/query",
        headers={"X-API-Key": "test-api-key"},
        json={"question": "How is the portfolio doing?"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "answer": "The portfolio contains the supplied dispute.",
        "based_on": {"dispute_count": 1, "stats_snapshot": True},
    }
    summary = received["context"]["disputes"][0]
    assert summary == {
        "chargeback_id": "cb_assistant_001",
        "decision": "FIGHT",
        "win_probability": 0.8,
        "expected_value": 120.0,
        "final_outcome": "WIN",
        "contradiction_summary": "Delivery signature is on file.",
        "decision_reasoning": "Expected recovery exceeds response cost.",
        "filing_deadline": None,
        "dispute_amount": None,
        "currency": None,
        "synthetic": False,
        "degraded_reasons": [],
        "device_risk": {"fraud_score": None, "vpn_detected": None, "geolocation_match": None},
    }


def test_missing_scoped_dispute_is_explicit_in_context() -> None:
    assert store.create_dispute(_state("cb_assistant_existing"))

    context = build_assistant_context("cb_assistant_missing")

    assert context["requested_chargeback_id"] == "cb_assistant_missing"
    assert context["requested_chargeback_found"] is False
    assert [item["chargeback_id"] for item in context["disputes"]] == ["cb_assistant_existing"]


def test_assistant_context_excludes_raw_evidence_and_pii() -> None:
    assert store.create_dispute(_state("cb_assistant_redacted"))

    context = build_assistant_context()
    serialized = str(context)

    assert "provider_secret" not in serialized
    assert "buyer@example.test" not in serialized
    assert "192.0.2.10" not in serialized
    assert "device-secret" not in serialized


def test_assistant_rate_limit_rejects_eleventh_request(monkeypatch) -> None:
    monkeypatch.setenv("ASSISTANT_RATE_LIMIT_PER_MINUTE", "10")

    for _ in range(10):
        enforce_assistant_rate_limit("test-api-key")

    with pytest.raises(HTTPException) as raised:
        enforce_assistant_rate_limit("test-api-key")

    assert raised.value.status_code == 429
    assert raised.value.headers["Retry-After"]


def test_openai_compatible_portfolio_assistant() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "The portfolio is healthy."}}]},
        )

    client = PortfolioAssistantClient.from_env(
        {
            "PORTFOLIO_ASSISTANT_API_KEY": "gsk_test",
            "PORTFOLIO_ASSISTANT_MODEL": "openai/gpt-oss-120b",
            "PORTFOLIO_ASSISTANT_BASE_URL": "https://api.groq.com/openai/v1/",
            "PORTFOLIO_ASSISTANT_REASONING_EFFORT": "low",
        }
    )
    client._client = httpx.Client(transport=httpx.MockTransport(handler))

    assert client.answer("How is the portfolio?", {"disputes": []}) == "The portfolio is healthy."
    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert captured["authorization"] == "Bearer gsk_test"
    assert captured["payload"]["messages"][0]["role"] == "system"
    assert captured["payload"]["reasoning_effort"] == "low"
    assert captured["payload"]["max_tokens"] == 1500


def test_context_is_bounded_and_scoped_with_safe_device_signals() -> None:
    for index in range(25):
        state = _state(f"disp_SIM_{index}")
        state["device"] = {"fraud_score": 85, "vpn_detected": True,
                           "geolocation_match": False, "ip_address": "192.0.2.8",
                           "device_fingerprint": "secret-device"}
        state["filing_deadline"] = datetime(2026, 9, 20, tzinfo=timezone.utc)
        store.create_dispute(state)
    assert len(build_assistant_context()["disputes"]) == 20
    scoped = build_assistant_context("disp_SIM_5")["disputes"]
    assert len(scoped) == 1
    assert scoped[0]["synthetic"] is True
    assert scoped[0]["filing_deadline"].startswith("2026-09-20")
    assert scoped[0]["device_risk"]["vpn_detected"] is True
    assert "192.0.2.8" not in json.dumps(scoped)
    assert "secret-device" not in json.dumps(scoped)


def test_status_requires_auth_and_never_returns_provider_keys(monkeypatch) -> None:
    monkeypatch.setenv("API_KEY", "test-api-key")
    monkeypatch.setenv("PORTFOLIO_ASSISTANT_USE_STUBS", "false")
    monkeypatch.setenv("PORTFOLIO_ASSISTANT_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("PORTFOLIO_ASSISTANT_MODEL", "test-model")
    monkeypatch.setenv("PORTFOLIO_ASSISTANT_API_KEY", "private-provider-key")
    monkeypatch.setenv("LLM_DECISION_REVIEW_ENABLED", "false")
    client = TestClient(app)
    assert client.get("/assistant/status").status_code == 401
    result = client.get("/assistant/status", headers={"X-API-Key": "test-api-key"})
    assert result.json()["guard_ai"] == {"mode": "live_configured", "model": "test-model"}
    assert result.json()["decision_review"]["mode"] == "disabled"
    assert "private-provider-key" not in result.text
    monkeypatch.delenv("PORTFOLIO_ASSISTANT_API_KEY")
    assert client.get("/assistant/status", headers={"X-API-Key": "test-api-key"}).json()["guard_ai"]["mode"] == "unavailable"


def test_generation_errors_do_not_log_provider_body(monkeypatch, caplog) -> None:
    monkeypatch.setenv("API_KEY", "test-api-key")
    def fail(*args):
        raise RuntimeError("private-key-in-provider-body")
    monkeypatch.setattr(assistant, "generate_portfolio_answer", fail)
    response = TestClient(app).post("/assistant/query", headers={"X-API-Key": "test-api-key"},
                                    json={"question": "Summarize the portfolio"})
    assert response.status_code == 503
    assert "private-key-in-provider-body" not in caplog.text + response.text


@pytest.mark.parametrize("content", [None, {}, 17, [], ""])
def test_nontext_provider_response_fails_safely(content) -> None:
    from integrations.portfolio_assistant import PortfolioAssistantRequestError
    with pytest.raises(PortfolioAssistantRequestError):
        PortfolioAssistantClient._extract_text({"choices": [{"message": {"content": content}}]})
