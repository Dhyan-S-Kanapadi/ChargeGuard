from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from api.store import store
from api.webhooks import build_initial_state
from integrations.reason_classification import (
    ReasonClassificationClient,
    ReasonClassificationConfigError,
    ReasonClassificationRequestError,
    ReasonClassificationResult,
    classification_facts,
)
from main import app


AUTH = {"X-API-Key": "test-api-key"}


@pytest.fixture(autouse=True)
def reset_reason_classification(monkeypatch):
    store.clear()
    monkeypatch.setenv("API_KEY", "test-api-key")
    monkeypatch.setenv("REASON_CLASSIFICATION_ENABLED", "true")
    monkeypatch.setenv("REASON_CLASSIFICATION_USE_STUBS", "false")
    monkeypatch.setenv("REASON_CLASSIFICATION_MIN_CONFIDENCE", "0.85")
    yield
    store.clear()


def _classification_state(**updates):
    now = datetime.now(timezone.utc)
    state = build_initial_state(
        chargeback_id="disp_reason_1",
        order_id=None,
        payment_id="pay_reason_1",
        reason_code="",
        card_network="VISA",
        dispute_amount=2500.0,
        currency="INR",
        filing_deadline=now + timedelta(days=5),
        merchant_profile={
            "merchant_id": "merchant_reason",
            "name": "Reason Merchant",
            "vertical": "ecommerce",
            "freshdesk_domain": "",
            "average_order_value": 1000.0,
            "chargeback_history_count": 0,
        },
        evidence_collection_degraded=True,
        degraded_reasons=[
            "network_reason_code_unavailable",
            "network_playbook_unavailable",
        ],
    )
    state.update(
        {
            "provider": "razorpay",
            "provider_event": "payment.dispute.created",
            "provider_reason_code": "unauthorised_transaction",
            "provider_status": "open",
            "provider_phase": "chargeback",
            "payment_rail": "CARD",
            "provider_respond_by": now + timedelta(days=5),
            "deadline_overdue": False,
            "decision": "ESCALATE_DEGRADED",
            "requires_human_review": True,
        }
    )
    state.update(updates)
    assert store.create_dispute(state)
    store.update_dispute(state["chargeback_id"], status="completed", state=state)
    return state


def _result(code="10.4", confidence=0.91, cannot_classify=False):
    return ReasonClassificationResult(
        recommended_reason_code=None if cannot_classify else code,
        confidence=confidence,
        rationale="Provider reason aligns with the allowlisted card-not-present category.",
        evidence_fields_used=["provider_reason_code"],
        cannot_classify=cannot_classify,
    )


def test_eligible_suggestion_is_allowlisted_persisted_and_reused(monkeypatch) -> None:
    _classification_state(customer_email="secret@example.com", shopify_admin_api_token="secret")
    calls = []

    def generate(state, candidates):
        calls.append((state, candidates))
        return _result(), "test-model"

    monkeypatch.setattr("api.disputes.generate_reason_recommendation", generate)
    client = TestClient(app)
    first = client.post(
        "/disputes/disp_reason_1/classification/suggestion",
        json={"actor_id": "operator-1"},
        headers=AUTH,
    )
    second = client.post(
        "/disputes/disp_reason_1/classification/suggestion",
        json={"actor_id": "operator-2"},
        headers=AUTH,
    )

    assert first.status_code == 200
    assert first.json()["recommended_reason_code"] == "10.4"
    assert first.json()["can_approve"] is True
    assert second.json()["suggestion_id"] == first.json()["suggestion_id"]
    assert len(calls) == 1
    assert {item["reason_code"] for item in calls[0][1]} == {"10.4", "13.1", "13.3"}
    state = store.get_dispute("disp_reason_1")["state"]
    assert state["reason_code"] == ""
    assert state.get("network_reason_code") is None
    assert state.get("classification_resume_scheduled") is not True


def test_suggestion_generation_is_authenticated_and_never_starts_graph(monkeypatch) -> None:
    _classification_state()
    graph_calls = []
    monkeypatch.setattr(
        "api.disputes.generate_reason_recommendation",
        lambda state, candidates: (_result(), "test-model"),
    )
    monkeypatch.setattr(
        "api.disputes.run_chargeback_graph",
        lambda state: graph_calls.append(state["chargeback_id"]),
    )

    response = TestClient(app).post(
        "/disputes/disp_reason_1/classification/suggestion",
        json={"actor_id": "operator-1"},
    )
    authenticated = TestClient(app).post(
        "/disputes/disp_reason_1/classification/suggestion",
        json={"actor_id": "operator-1"},
        headers=AUTH,
    )

    assert response.status_code == 401
    assert authenticated.status_code == 200
    assert graph_calls == []


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"card_network": None}, "verified_card_network_unavailable"),
        ({"payment_rail": "UPI"}, "non_card_payment"),
        ({"provider_respond_by": None}, "filing_deadline_unavailable"),
        ({"deadline_overdue": True}, "filing_deadline_overdue"),
        (
            {
                "degraded_reasons": [
                    "network_reason_code_unavailable",
                    "razorpay_payment_enrichment_failed",
                ]
            },
            "unrelated_manual_review_blocker",
        ),
    ],
)
def test_ineligible_disputes_do_not_call_the_model(monkeypatch, updates, reason) -> None:
    _classification_state(**updates)
    monkeypatch.setattr(
        "api.disputes.generate_reason_recommendation",
        lambda state, candidates: pytest.fail("model must not be called"),
    )

    response = TestClient(app).post(
        "/disputes/disp_reason_1/classification/suggestion",
        json={"actor_id": "operator-1"},
        headers=AUTH,
    )

    assert response.status_code == 409
    assert reason in response.json()["detail"]


def test_existing_authoritative_reason_does_not_call_the_model(monkeypatch) -> None:
    _classification_state(network_reason_code="10.4", reason_code="10.4")
    monkeypatch.setattr(
        "api.disputes.generate_reason_recommendation",
        lambda state, candidates: pytest.fail("model must not be called"),
    )

    response = TestClient(app).post(
        "/disputes/disp_reason_1/classification/suggestion",
        json={"actor_id": "operator-1"},
        headers=AUTH,
    )

    assert response.status_code == 409
    assert "network_reason_code_already_available" in response.json()["detail"]


def test_low_confidence_is_not_approvable(monkeypatch) -> None:
    _classification_state()
    monkeypatch.setattr(
        "api.disputes.generate_reason_recommendation",
        lambda state, candidates: (_result(confidence=0.4), "test-model"),
    )
    client = TestClient(app)
    low = client.post(
        "/disputes/disp_reason_1/classification/suggestion",
        json={"actor_id": "operator-1"},
        headers=AUTH,
    )

    assert low.status_code == 200
    assert low.json()["status"] == "unavailable"
    assert low.json()["can_approve"] is False
    assert low.json()["unavailability_reason"] == "confidence_below_threshold"


def test_cannot_classify_is_persisted_as_unavailable(monkeypatch) -> None:
    _classification_state()
    monkeypatch.setattr(
        "api.disputes.generate_reason_recommendation",
        lambda state, candidates: (_result(confidence=0.2, cannot_classify=True), "test-model"),
    )

    response = TestClient(app).post(
        "/disputes/disp_reason_1/classification/suggestion",
        json={"actor_id": "operator-1"},
        headers=AUTH,
    )

    assert response.status_code == 200
    assert response.json()["recommended_reason_code"] is None
    assert response.json()["can_approve"] is False
    assert response.json()["unavailability_reason"] == "model_could_not_classify"


def test_disabled_or_missing_credentials_preserves_manual_state(monkeypatch) -> None:
    _classification_state()
    client = TestClient(app)
    monkeypatch.setenv("REASON_CLASSIFICATION_ENABLED", "false")
    disabled = client.post(
        "/disputes/disp_reason_1/classification/suggestion",
        json={"actor_id": "operator-1"},
        headers=AUTH,
    )
    monkeypatch.setenv("REASON_CLASSIFICATION_ENABLED", "true")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    missing_key = client.post(
        "/disputes/disp_reason_1/classification/suggestion",
        json={"actor_id": "operator-1"},
        headers=AUTH,
    )

    assert disabled.status_code == 503
    assert missing_key.status_code == 503
    state = store.get_dispute("disp_reason_1")["state"]
    assert state["decision"] == "ESCALATE_DEGRADED"
    assert state["reason_code"] == ""
    assert state.get("classification_suggestion") is None
    assert state.get("classification_resume_scheduled") is not True


def test_code_without_verified_playbook_and_template_fails_safely(monkeypatch) -> None:
    _classification_state()
    monkeypatch.setattr(
        "api.disputes.reason_classification_candidates",
        lambda network: [{"reason_code": "99.9", "description": "Fake", "summary": "Fake"}],
    )
    monkeypatch.setattr(
        "api.disputes.generate_reason_recommendation",
        lambda state, candidates: (_result(code="99.9"), "test-model"),
    )

    response = TestClient(app).post(
        "/disputes/disp_reason_1/classification/suggestion",
        json={"actor_id": "operator-1"},
        headers=AUTH,
    )

    assert response.status_code == 503
    state = store.get_dispute("disp_reason_1")["state"]
    assert state.get("classification_suggestion") is None
    assert state.get("classification_resume_scheduled") is not True


def test_matching_approval_resumes_once_and_mismatches_are_rejected(monkeypatch) -> None:
    _classification_state()
    graph_calls = []
    monkeypatch.setattr(
        "api.disputes.generate_reason_recommendation",
        lambda state, candidates: (_result(), "test-model"),
    )
    monkeypatch.setattr(
        "api.disputes.run_chargeback_graph",
        lambda state: graph_calls.append(state["chargeback_id"]),
    )
    client = TestClient(app)
    suggestion = client.post(
        "/disputes/disp_reason_1/classification/suggestion",
        json={"actor_id": "requester"},
        headers=AUTH,
    ).json()
    base = {
        "card_network": "VISA",
        "network_reason_code": "10.4",
        "actor_id": "approver",
        "suggestion_id": suggestion["suggestion_id"],
    }

    mismatch_id = client.post(
        "/disputes/disp_reason_1/classification",
        json={**base, "suggestion_id": "rcs_wrong"},
        headers=AUTH,
    )
    mismatch_code = client.post(
        "/disputes/disp_reason_1/classification",
        json={**base, "network_reason_code": "13.1"},
        headers=AUTH,
    )
    mismatch_network = client.post(
        "/disputes/disp_reason_1/classification",
        json={**base, "card_network": "MASTERCARD", "network_reason_code": "4853"},
        headers=AUTH,
    )
    approved = client.post(
        "/disputes/disp_reason_1/classification", json=base, headers=AUTH
    )
    duplicate = client.post(
        "/disputes/disp_reason_1/classification", json=base, headers=AUTH
    )

    assert mismatch_id.status_code == 409
    assert mismatch_code.status_code == 409
    assert mismatch_network.status_code == 409
    assert approved.status_code == 200
    assert duplicate.status_code == 409
    assert graph_calls == ["disp_reason_1"]
    state = store.get_dispute("disp_reason_1")["state"]
    assert state["classification_suggestion"]["status"] == "approved"
    assert state["classification_audit"]["source"] == "llm_assisted_operator"
    assert state["classification_audit"]["actor_id"] == "approver"


def test_new_unrelated_blocker_prevents_pending_suggestion_approval(monkeypatch) -> None:
    _classification_state()
    graph_calls = []
    monkeypatch.setattr(
        "api.disputes.generate_reason_recommendation",
        lambda state, candidates: (_result(), "test-model"),
    )
    monkeypatch.setattr(
        "api.disputes.run_chargeback_graph",
        lambda state: graph_calls.append(state["chargeback_id"]),
    )
    client = TestClient(app)
    suggestion = client.post(
        "/disputes/disp_reason_1/classification/suggestion",
        json={"actor_id": "requester"},
        headers=AUTH,
    ).json()
    record = store.get_dispute("disp_reason_1")
    record["state"]["degraded_reasons"].append("razorpay_payment_enrichment_failed")
    store.update_dispute("disp_reason_1", status="completed", state=record["state"])

    response = client.post(
        "/disputes/disp_reason_1/classification",
        json={
            "card_network": "VISA",
            "network_reason_code": "10.4",
            "actor_id": "approver",
            "suggestion_id": suggestion["suggestion_id"],
        },
        headers=AUTH,
    )

    assert response.status_code == 409
    assert graph_calls == []
    assert store.get_dispute("disp_reason_1")["state"]["reason_code"] == ""


def test_operator_can_reject_without_resuming_graph(monkeypatch) -> None:
    _classification_state()
    monkeypatch.setattr(
        "api.disputes.generate_reason_recommendation",
        lambda state, candidates: (_result(), "test-model"),
    )
    client = TestClient(app)
    suggestion = client.post(
        "/disputes/disp_reason_1/classification/suggestion",
        json={"actor_id": "requester"},
        headers=AUTH,
    ).json()

    response = client.post(
        "/disputes/disp_reason_1/classification/suggestion/reject",
        json={"actor_id": "reviewer", "suggestion_id": suggestion["suggestion_id"]},
        headers=AUTH,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    state = store.get_dispute("disp_reason_1")["state"]
    assert state["reason_code"] == ""
    assert state.get("classification_resume_scheduled") is not True


def _anthropic_response(input_value):
    return httpx.Response(
        200,
        json={
            "content": [
                {
                    "type": "tool_use",
                    "name": "return_reason_classification",
                    "input": input_value,
                }
            ]
        },
    )


def test_client_rejects_outside_allowlist_and_malformed_output() -> None:
    responses = [
        _anthropic_response(_result(code="99.9").model_dump()),
        _anthropic_response({**_result().model_dump(), "unexpected": True}),
    ]

    def handler(request):
        return responses.pop(0)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = ReasonClassificationClient(api_key="test", client=http_client)
        facts = classification_facts(
            {"card_network": "VISA", "payment_rail": "CARD", "provider_reason_code": "fraud"},
            [{"reason_code": "10.4", "description": "Fraud", "summary": "CNP"}],
        )
        with pytest.raises(ReasonClassificationRequestError, match="outside the allowlist"):
            client.recommend(facts, allowed_codes=["10.4"])
        with pytest.raises(ReasonClassificationRequestError, match="schema validation"):
            client.recommend(facts, allowed_codes=["10.4"])


def test_client_sends_only_bounded_non_pii_facts() -> None:
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return _anthropic_response(_result().model_dump())

    state = {
        "card_network": "VISA",
        "payment_rail": "CARD",
        "provider_reason_code": "unauthorised_transaction",
        "customer_email": "secret@example.com",
        "customer_ip": "203.0.113.1",
        "merchant_profile": {"shopify_admin_api_token": "secret-token"},
        "transaction": {"raw": "secret-raw-body"},
    }
    candidates = [{"reason_code": "10.4", "description": "Fraud", "summary": "CNP"}]
    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = ReasonClassificationClient(api_key="test", client=http_client)
        client.recommend(classification_facts(state, candidates), allowed_codes=["10.4"])

    serialized = json.dumps(captured)
    assert "unauthorised_transaction" in serialized
    assert "secret@example.com" not in serialized
    assert "203.0.113.1" not in serialized
    assert "secret-token" not in serialized
    assert "secret-raw-body" not in serialized


def test_client_timeout_provider_error_and_missing_key_fail_safely() -> None:
    facts = classification_facts(
        {"card_network": "VISA", "payment_rail": "CARD", "provider_reason_code": "fraud"},
        [{"reason_code": "10.4", "description": "Fraud", "summary": "CNP"}],
    )

    def timeout(request):
        raise httpx.ReadTimeout("secret provider body", request=request)

    with httpx.Client(transport=httpx.MockTransport(timeout)) as http_client:
        client = ReasonClassificationClient(api_key="test", client=http_client)
        with pytest.raises(ReasonClassificationRequestError, match="timed out") as exc_info:
            client.recommend(facts, allowed_codes=["10.4"])
        assert "secret provider body" not in str(exc_info.value)

    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(500, text="secret body"))
    ) as http_client:
        client = ReasonClassificationClient(api_key="test", client=http_client)
        with pytest.raises(ReasonClassificationRequestError) as exc_info:
            client.recommend(facts, allowed_codes=["10.4"])
        assert "secret body" not in str(exc_info.value)

    with pytest.raises(ReasonClassificationConfigError):
        ReasonClassificationClient.from_env({})
