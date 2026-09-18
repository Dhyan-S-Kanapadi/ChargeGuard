from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from api import webhooks
from api.store import InMemoryStore, store
from core.state import MerchantProfile
from integrations.razorpay import RazorpayRequestError
from integrations.razorpay_schemas import RazorpayPaymentEntity
from integrations.razorpay_webhook import parse_envelope
from integrations.credential_secrets import reset_credential_secret_store_cache
from main import app


SECRET = "webhook-test-secret"


@pytest.fixture
def official_event():
    # Official shape, synthetic identifiers/contact; timestamps intentionally expired.
    # https://razorpay.com/docs/webhooks/disputes (checked 2026-09-18)
    return json.loads((Path(__file__).parent / "fixtures" / "razorpay_official_dispute.json").read_text())


@pytest.mark.parametrize("event", ["created", "won", "lost", "closed", "under_review", "action_required"])
def test_official_shapes_are_queued_once_before_processing(monkeypatch, official_event, event):
    from api import razorpay_webhooks

    official_event["event"] = f"payment.dispute.{event}"
    dispute = official_event["payload"]["dispute"]
    dispute["entity"]["status"] = "open" if event in {"created", "action_required"} else event
    if event in {"won", "under_review", "closed"}:
        official_event["payload"]["payment"]["entity"].update(order_id=None, fee=None, tax=None)
    if event == "action_required":
        dispute["evidence"] = dispute["entity"].pop("evidence")
    scheduled = []

    def enqueue(tasks, event_id):
        saved = store.get_provider_event(event_id)
        assert saved["processing_state"] == "queued"
        assert saved["event_data"]["payload"]["payment"]["entity"]["notes"] == {}
        assert "fixture@example.invalid" not in json.dumps(saved, default=str)
        scheduled.append(event_id)

    monkeypatch.setattr(razorpay_webhooks, "enqueue_razorpay_provider_event", enqueue)
    raw = json.dumps(official_event).encode()
    assert _post(raw).status_code == 202
    assert _post(raw).json()["status"] == "duplicate"
    assert scheduled == ["evt_1"]
    assert store.get_dispute("disp_connectivity_fixture") is None


@pytest.mark.parametrize("notes", [[], {}, {"commerce_order_id": "fixture-order", "nested": {"a": 1}}])
def test_payment_notes_normalization_preserves_dictionaries(official_event, notes):
    payment = official_event["payload"]["payment"]["entity"]
    payment["notes"] = notes
    parsed = parse_envelope(json.dumps(official_event).encode())
    assert parsed.payload.payment.entity.notes == ({} if notes == [] else notes)
    # The same entity schema protects REST enrichment and stored-envelope parsing.
    assert RazorpayPaymentEntity.model_validate(payment).notes == parsed.payload.payment.entity.notes
    del payment["notes"]
    assert RazorpayPaymentEntity.model_validate(payment).notes == {}


@pytest.mark.parametrize("notes", [["bad"], [["key", "value"]], [{}], None, "", "{}", 0, False])
def test_signed_malformed_notes_rejected_without_persistence(official_event, notes):
    official_event["payload"]["payment"]["entity"]["notes"] = notes
    assert _post(json.dumps(official_event).encode()).status_code == 422
    assert store.list_provider_events() == []


def test_official_empty_notes_normalization_cannot_bypass_signature(official_event):
    raw = json.dumps(official_event).encode()
    official_event["payload"]["payment"]["entity"]["notes"] = {}
    changed = json.dumps(official_event).encode()
    assert TestClient(app).post("/webhook/razorpay", content=changed, headers=_headers(raw)).status_code == 401
    assert store.list_provider_events() == []


@pytest.mark.parametrize("provider_status", [200, 401, 404])
def test_official_sample_recovers_with_owned_test_connector(
    monkeypatch, tmp_path, official_event, provider_status,
):
    from integrations.razorpay import RazorpayClient

    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "true")
    monkeypatch.setenv("RAZORPAY_USE_STUBS", "false")
    monkeypatch.setenv("ALLOW_GLOBAL_PAYMENT_CREDENTIAL_FALLBACK", "false")
    monkeypatch.setenv("CASE_SUMMARY_USE_STUBS", "true")
    monkeypatch.setenv("CHARGEGUARD_CREDENTIAL_STORE_PATH", str(tmp_path / "secrets.json"))
    monkeypatch.setenv("CHARGEGUARD_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    reset_credential_secret_store_cache()
    calls = []

    def request(self, method, path, **kwargs):
        # Real connector resolver and client; all HTTP is intercepted here.
        assert self.key_id == "rzp_test_connectivityfixture"
        assert method == "GET"  # No accept, contest or upload is allowed.
        calls.append(path)
        if path == "/payments":
            return {"entity": "collection", "items": []}
        assert path == "/payments/pay_connectivity_fixture"
        if provider_status != 200:
            raise RazorpayRequestError("inaccessible sample", status_code=provider_status)
        return {"id": "pay_connectivity_fixture", "method": "card", "notes": [],
                "order_id": "order_connectivity_fixture", "card": {"network": "Visa"}}

    monkeypatch.setattr(RazorpayClient, "_request", request)
    monkeypatch.setattr(webhooks, "run_chargeback_graph", lambda state: pytest.fail("Sample must require human review"))
    try:
        raw = json.dumps(official_event).encode()
        assert _post(raw).status_code == 202
        assert store.get_provider_event("evt_1")["processing_state"] == "unresolved"
        assert calls == []
        client = TestClient(app, headers={"X-API-Key": "test-api-key"})
        assert client.post("/merchants", json=_merchant("acc_connectivityfixture")).status_code == 201
        connected = client.post("/merchants/merchant_rzp/payment-connectors/razorpay", json={
            "key_id": "rzp_test_connectivityfixture", "key_secret": "fixture-only-secret",
            "razorpay_account_id": "acc_connectivityfixture",
        })
        assert connected.status_code == 201
        assert client.post("/internal/razorpay/events/evt_1/retry").status_code == 200
        assert calls == ["/payments", "/payments/pay_connectivity_fixture"]
        assert store.get_provider_event("evt_1")["processing_state"] == "manual_review"
        state = store.get_dispute("disp_connectivity_fixture")["state"]
        assert state["data_environment"] == "staging"
        assert state["decision"] == "ESCALATE_DEGRADED"
        assert state["final_outcome"] == "PENDING"
        assert "respond_by_overdue" in state["degraded_reasons"]
        assert "network_reason_code_unavailable" in state["degraded_reasons"]
        assert not state.get("filed_at")
        if provider_status == 200:
            assert state["card_network"] == "VISA"
        else:
            assert state.get("card_network") is None
            assert "razorpay_payment_enrichment_failed" in state["degraded_reasons"]
        assert _post(raw).json()["status"] == "duplicate"
        assert len(calls) == 2
    finally:
        reset_credential_secret_store_cache()


@pytest.fixture(autouse=True)
def reset_store(monkeypatch):
    store.clear()
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("RAZORPAY_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("API_KEY", "test-api-key")
    monkeypatch.delenv("RAZORPAY_SIMULATOR_ENABLED", raising=False)
    yield
    store.clear()


def _merchant(account_id: str = "acc_test") -> MerchantProfile:
    return {
        "merchant_id": "merchant_rzp",
        "name": "Razorpay Merchant",
        "vertical": "ecommerce",
        "payment_provider": "razorpay",
        "razorpay_account_id": account_id,
        "freshdesk_domain": "",
        "average_order_value": 1000.0,
        "chargeback_history_count": 0,
    }


def _raw_event(
    *,
    account_id: str = "acc_test",
    event: str = "payment.dispute.created",
    dispute_id: str = "disp_1",
    method: str = "card",
    network: str | None = "Visa",
    simulator: bool = False,
    respond_by: datetime | None = None,
    event_created_at: datetime | None = None,
    include_payment: bool = True,
    padding: int = 0,
) -> bytes:
    now = event_created_at or datetime.now(timezone.utc)
    notes = {}
    if simulator:
        notes = {
            "chargeguard_simulator": True,
            "chargeguard_card_network": network,
            "chargeguard_network_reason_code": "10.4",
        }
    payment = {
        "id": "pay_1",
        "amount": 250000,
        "currency": "INR",
        "order_id": "order_1",
        "method": method,
        "notes": notes,
        "created_at": int((now - timedelta(days=10)).timestamp()),
    }
    if method == "card" and network:
        payment["card"] = {"network": network}
    payload = {
        "entity": "event",
        "account_id": account_id,
        "event": event,
        "contains": ["payment", "dispute"],
        "payload": {
            "payment": {"entity": payment} if include_payment else None,
            "dispute": {
                "entity": {
                    "id": dispute_id,
                    "entity": "dispute",
                    "payment_id": "pay_1",
                    "amount": 250000,
                    "currency": "INR",
                    "reason_code": "unauthorised_transaction",
                    "respond_by": int(
                        (respond_by or now + timedelta(days=5)).timestamp()
                    ),
                    "status": event.rsplit(".", 1)[-1].replace("created", "open"),
                    "phase": "chargeback",
                    "created_at": int((now - timedelta(days=1)).timestamp()),
                }
            },
        },
        "created_at": int(now.timestamp()),
    }
    if padding:
        payload["padding"] = "x" * padding
    return json.dumps(payload, separators=(",", ":")).encode()


def _headers(raw: bytes, event_id: str | None = "evt_1") -> dict[str, str]:
    headers = {
        "X-Razorpay-Signature": hmac.new(
            SECRET.encode(), raw, hashlib.sha256
        ).hexdigest()
    }
    if event_id:
        headers["x-razorpay-event-id"] = event_id
    return headers


def _post(raw: bytes, event_id: str | None = "evt_1"):
    return TestClient(app).post(
        "/webhook/razorpay",
        content=raw,
        headers=_headers(raw, event_id),
    )


def _automation_event(monkeypatch, **kwargs) -> bytes:
    monkeypatch.setenv("RAZORPAY_SIMULATOR_ENABLED", "true")
    return _raw_event(dispute_id="disp_SIM_1", simulator=True, **kwargs)


def test_valid_signature_maps_decimal_amount_and_timestamp(monkeypatch) -> None:
    assert store.create_merchant(_merchant())
    calls: list[str] = []
    monkeypatch.setattr(
        webhooks,
        "run_chargeback_graph",
        lambda state: calls.append(state["chargeback_id"]),
    )
    raw = _automation_event(monkeypatch)

    response = _post(raw)

    assert response.status_code == 202
    assert calls == ["disp_SIM_1"]
    state = store.get_dispute("disp_SIM_1")["state"]
    assert state["dispute_amount"] == 2500.0
    assert state["provider_reason_code"] == "unauthorised_transaction"
    assert state["network_reason_code"] == "10.4"
    assert state["reason_code"] == "10.4"
    assert state["provider_event_timestamp"].tzinfo is not None


def test_invalid_missing_or_changed_signature_is_rejected() -> None:
    raw = _raw_event()
    client = TestClient(app)
    assert client.post("/webhook/razorpay", content=raw).status_code == 401
    assert client.post(
        "/webhook/razorpay",
        content=raw,
        headers={"X-Razorpay-Signature": "bad"},
    ).status_code == 401
    assert client.post(
        "/webhook/razorpay",
        content=raw + b" ",
        headers=_headers(raw),
    ).status_code == 401


@pytest.mark.parametrize("dispute_id,simulator", [("disp_SIM_1", False), ("disp_1", True)])
def test_production_rejects_signed_synthetic_event_before_claim(monkeypatch, dispute_id, simulator):
    monkeypatch.setenv("ENVIRONMENT", "production")
    raw = _raw_event(dispute_id=dispute_id, simulator=simulator)
    assert _post(raw).status_code == 422
    assert store.list_provider_events() == []


def test_missing_event_id_uses_payload_hash_and_deduplicates(monkeypatch) -> None:
    assert store.create_merchant(_merchant())
    monkeypatch.setattr(webhooks, "run_chargeback_graph", lambda state: None)
    raw = _automation_event(monkeypatch)

    first = _post(raw, event_id=None)
    duplicate = _post(raw, event_id=None)

    assert first.status_code == 202
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "duplicate"
    assert duplicate.json()["event_id"] == f"sha256:{hashlib.sha256(raw).hexdigest()}"


def test_unknown_merchant_is_persisted_and_acknowledged() -> None:
    raw = _raw_event(account_id="acc_unknown")

    response = _post(raw)

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert store.get_dispute("disp_1") is None
    event = store.get_provider_event("evt_1")
    assert event["processing_state"] == "unresolved"
    assert event["provider_dispute_id"] == "disp_1"


def test_unsupported_authentic_event_is_recorded_and_ignored() -> None:
    raw = _raw_event(event="payment.dispute.future_status")

    response = _post(raw)

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert store.get_provider_event("evt_1")["processing_state"] == "ignored"


def test_production_reason_is_not_silently_mapped_to_network_code() -> None:
    assert store.create_merchant(_merchant())

    response = _post(_raw_event())

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert store.get_provider_event("evt_1")["processing_state"] == "manual_review"
    state = store.get_dispute("disp_1")["state"]
    assert state["provider_reason_code"] == "unauthorised_transaction"
    assert state["reason_code"] == ""
    assert state["card_network"] == "VISA"
    assert state["decision"] == "ESCALATE_DEGRADED"


def test_verified_configured_reason_mapping_schedules_graph(
    monkeypatch, tmp_path
) -> None:
    assert store.create_merchant(_merchant())
    mapping_path = tmp_path / "razorpay_reason_mappings.json"
    mapping_path.write_text(
        json.dumps(
            {
                "version": "verified-test-v1",
                "mappings": [
                    {
                        "provider": "razorpay",
                        "network": "VISA",
                        "provider_reason_code": "unauthorised_transaction",
                        "network_reason_code": "10.4",
                        "source": "verified_test_fixture",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("RAZORPAY_REASON_MAPPING_PATH", str(mapping_path))
    calls: list[str] = []
    monkeypatch.setattr(
        webhooks,
        "run_chargeback_graph",
        lambda state: calls.append(state["chargeback_id"]),
    )

    response = _post(_raw_event())

    assert response.status_code == 202
    assert calls == ["disp_1"]
    state = store.get_dispute("disp_1")["state"]
    assert state["network_reason_code"] == "10.4"
    assert state["reason_mapping_version"] == "verified-test-v1"
    assert state["reason_mapping_source"] == "verified_test_fixture"


def test_authenticated_classification_validates_and_resumes_exactly_once(
    monkeypatch,
) -> None:
    assert store.create_merchant(_merchant())
    assert _post(_raw_event()).status_code == 202
    calls: list[str] = []
    monkeypatch.setattr(
        "api.disputes.run_chargeback_graph",
        lambda state: calls.append(state["chargeback_id"]),
    )
    client = TestClient(app)
    payload = {
        "card_network": "VISA",
        "network_reason_code": "10.4",
        "actor_id": "risk-operator-17",
    }

    assert client.post("/disputes/disp_1/classification", json=payload).status_code == 401
    invalid = client.post(
        "/disputes/disp_1/classification",
        json={**payload, "network_reason_code": "not-supported"},
        headers={"X-API-Key": "test-api-key"},
    )
    assert invalid.status_code == 422
    classified = client.post(
        "/disputes/disp_1/classification",
        json=payload,
        headers={"X-API-Key": "test-api-key"},
    )
    duplicate = client.post(
        "/disputes/disp_1/classification",
        json=payload,
        headers={"X-API-Key": "test-api-key"},
    )

    assert classified.status_code == 200
    assert classified.json()["status"] == "scheduled"
    assert duplicate.status_code == 409
    assert calls == ["disp_1"]
    state = store.get_dispute("disp_1")["state"]
    assert state["reason_code"] == "10.4"
    assert state["classification_audit"]["actor_id"] == "risk-operator-17"
    assert state["classification_audit"]["source"] == "authenticated_operator"
    assert state["degraded_reasons"] == []


def test_classification_rejects_card_network_conflict() -> None:
    assert store.create_merchant(_merchant())
    assert _post(_raw_event()).status_code == 202

    response = TestClient(app).post(
        "/disputes/disp_1/classification",
        json={
            "card_network": "MASTERCARD",
            "network_reason_code": "4853",
            "actor_id": "risk-operator-17",
        },
        headers={"X-API-Key": "test-api-key"},
    )

    assert response.status_code == 409


def test_upi_dispute_never_becomes_rupay() -> None:
    assert store.create_merchant(_merchant())

    response = _post(_raw_event(method="upi", network=None))

    assert response.status_code == 202
    state = store.get_dispute("disp_1")["state"]
    assert state["payment_rail"] == "UPI"
    assert state["card_network"] is None
    assert state["decision"] == "ESCALATE_DEGRADED"


def test_card_network_is_enriched_from_expanded_payment(monkeypatch) -> None:
    assert store.create_merchant(_merchant())
    monkeypatch.setenv("ALLOW_GLOBAL_PAYMENT_CREDENTIAL_FALLBACK", "true")
    calls = []

    class FakeClient:
        def get_payment(self, payment_id, *, expand_card=False):
            calls.append((payment_id, expand_card))
            return {
                "id": payment_id,
                "order_id": "order_enriched",
                "method": "card",
                "card": {"network": "Mastercard"},
            }

    monkeypatch.setattr(
        "api.razorpay_service.RazorpayClient.from_env",
        lambda: FakeClient(),
    )

    response = _post(_raw_event(include_payment=False))

    assert response.status_code == 202
    assert calls == [("pay_1", True)]
    state = store.get_dispute("disp_1")["state"]
    assert state["card_network"] == "MASTERCARD"
    assert state["provider_order_id"] == "order_enriched"
    assert "order_id" not in state


def test_enrichment_failure_does_not_drop_event(monkeypatch) -> None:
    assert store.create_merchant(_merchant())
    monkeypatch.setenv("ALLOW_GLOBAL_PAYMENT_CREDENTIAL_FALLBACK", "true")

    class FailingClient:
        def get_payment(self, payment_id, *, expand_card=False):
            raise RazorpayRequestError("temporary Razorpay failure")

    monkeypatch.setattr(
        "api.razorpay_service.RazorpayClient.from_env",
        lambda: FailingClient(),
    )

    response = _post(_raw_event(include_payment=False))

    assert response.status_code == 202
    assert store.get_dispute("disp_1") is not None
    state = store.get_dispute("disp_1")["state"]
    assert "razorpay_payment_enrichment_failed" in state["degraded_reasons"]


def test_overdue_respond_by_is_ingested_for_manual_review() -> None:
    assert store.create_merchant(_merchant())
    overdue = datetime.now(timezone.utc) - timedelta(hours=1)

    response = _post(_raw_event(respond_by=overdue))

    assert response.status_code == 202
    state = store.get_dispute("disp_1")["state"]
    assert state["deadline_overdue"] is True
    assert "respond_by_overdue" in state["degraded_reasons"]


def test_created_starts_graph_exactly_once(monkeypatch) -> None:
    assert store.create_merchant(_merchant())
    calls: list[str] = []
    monkeypatch.setattr(
        webhooks,
        "run_chargeback_graph",
        lambda state: calls.append(state["chargeback_id"]),
    )
    raw = _automation_event(monkeypatch)

    assert _post(raw).status_code == 202
    assert _post(raw).json()["status"] == "duplicate"
    assert calls == ["disp_SIM_1"]


def test_lifecycle_updates_case_and_only_filed_terminal_outcome_learns(monkeypatch) -> None:
    assert store.create_merchant(_merchant())
    monkeypatch.setattr(webhooks, "run_chargeback_graph", lambda state: None)
    created_at = datetime.now(timezone.utc)
    assert _post(_automation_event(monkeypatch, event_created_at=created_at)).status_code == 202

    action = _raw_event(
        event="payment.dispute.action_required",
        dispute_id="disp_SIM_1",
        event_created_at=created_at + timedelta(minutes=1),
    )
    assert _post(action, "evt_action").status_code == 202
    assert store.get_dispute("disp_SIM_1")["state"]["provider_action_required"] is True

    won = _raw_event(
        event="payment.dispute.won",
        dispute_id="disp_SIM_1",
        event_created_at=created_at + timedelta(minutes=2),
    )
    response = _post(won, "evt_won_unfiled")
    assert response.status_code == 202
    assert store.get_provider_event("evt_won_unfiled")["processing_state"] == (
        "outcome_not_eligible"
    )
    assert store.get_dispute("disp_SIM_1")["state"]["final_outcome"] is None

    record = store.get_dispute("disp_SIM_1")
    state = record["state"]
    state["decision"] = "FIGHT"
    state["quality_approved"] = True
    state["filed_at"] = datetime.now(timezone.utc)
    state["filing_confirmation"] = "filed_visa_disp_SIM_1"
    store.update_dispute("disp_SIM_1", status="completed", state=state)
    lost = _raw_event(
        event="payment.dispute.lost",
        dispute_id="disp_SIM_1",
        event_created_at=created_at + timedelta(minutes=3),
    )
    assert _post(lost, "evt_lost_filed").status_code == 202
    assert store.get_dispute("disp_SIM_1")["state"]["final_outcome"] == "LOSS"


def test_closed_updates_status_without_inventing_outcome() -> None:
    assert store.create_merchant(_merchant())
    assert _post(_raw_event()).status_code == 202
    closed = _raw_event(event="payment.dispute.closed")

    response = _post(closed, "evt_closed")

    assert response.status_code == 202
    state = store.get_dispute("disp_1")["state"]
    assert state["provider_status"] == "closed"
    assert state["final_outcome"] == "PENDING"


def test_conflicting_terminal_event_does_not_regress_recorded_outcome() -> None:
    assert store.create_merchant(_merchant())
    assert _post(_raw_event()).status_code == 202
    record = store.get_dispute("disp_1")
    state = record["state"]
    state["decision"] = "FIGHT"
    state["quality_approved"] = True
    state["filed_at"] = datetime.now(timezone.utc)
    state["filing_confirmation"] = "filed_visa_disp_1"
    state["final_outcome"] = None
    store.update_dispute("disp_1", status="completed", state=state)
    won = _raw_event(event="payment.dispute.won")
    assert _post(won, "evt_won").status_code == 202
    lost = _raw_event(
        event="payment.dispute.lost",
        event_created_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )

    response = _post(lost, "evt_conflicting_lost")

    assert response.status_code == 202
    assert store.get_provider_event("evt_conflicting_lost")["processing_state"] == "stale"
    state = store.get_dispute("disp_1")["state"]
    assert state["final_outcome"] == "WIN"
    assert state["provider_event"] == "payment.dispute.won"


def test_out_of_order_created_does_not_regress_under_review(monkeypatch) -> None:
    assert store.create_merchant(_merchant())
    calls = []
    monkeypatch.setattr(webhooks, "run_chargeback_graph", lambda state: calls.append(state))
    now = datetime.now(timezone.utc)
    under_review = _raw_event(
        event="payment.dispute.under_review",
        event_created_at=now,
    )
    assert _post(under_review, "evt_review").status_code == 202
    older_created = _raw_event(event_created_at=now - timedelta(hours=1))

    assert _post(older_created, "evt_created_late").status_code == 202
    state = store.get_dispute("disp_1")["state"]
    assert state["provider_event"] == "payment.dispute.under_review"
    assert calls == []


def test_body_size_limit_is_enforced(monkeypatch) -> None:
    monkeypatch.setenv("RAZORPAY_WEBHOOK_MAX_BODY_BYTES", "1024")
    raw = _raw_event(padding=2000)
    assert _post(raw).status_code == 413


def test_provider_events_persist_with_canonical_fields(tmp_path) -> None:
    path = tmp_path / "store.json"
    local = InMemoryStore(path=path)
    assert local.create_merchant(_merchant())
    duplicate = _merchant()
    duplicate["merchant_id"] = "other"
    assert local.create_merchant(duplicate) is False
    assert local.claim_provider_event(
        {
            "event_id": "evt_saved",
            "provider": "razorpay",
            "event_type": "payment.dispute.created",
            "provider_dispute_id": "disp_saved",
            "payload_hash": "hash",
            "processing_state": "received",
        }
    )
    local.update_provider_event("evt_saved", processing_state="scheduled")

    reloaded = InMemoryStore(path=path)

    event = reloaded.get_provider_event("evt_saved")
    assert event["event_id"] == "evt_saved"
    assert event["event_type"] == "payment.dispute.created"
    assert event["provider_dispute_id"] == "disp_saved"
    assert event["processing_state"] == "scheduled"
    assert event["processed_at"] is not None


def test_failed_provider_event_can_be_reclaimed_for_retry() -> None:
    event = {
        "event_id": "evt_retry",
        "provider": "razorpay",
        "event_type": "payment.dispute.created",
        "provider_dispute_id": "disp_retry",
        "processing_state": "received",
    }
    assert store.claim_provider_event(event) is True
    store.update_provider_event(
        "evt_retry",
        processing_state="failed",
        failure_reason="temporary failure",
    )

    assert store.claim_provider_event(event) is True
    reclaimed = store.get_provider_event("evt_retry")
    assert reclaimed["processing_state"] == "received"
    assert reclaimed["failure_reason"] is None
    assert reclaimed["attempt_count"] == 0
    assert store.queue_provider_event("evt_retry") is True
    assert store.start_provider_event_processing("evt_retry") is True
    assert store.get_provider_event("evt_retry")["attempt_count"] == 1
