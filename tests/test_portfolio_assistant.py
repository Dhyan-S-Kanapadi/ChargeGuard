from datetime import datetime, timezone
from typing import cast

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
