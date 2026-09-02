from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from api import webhooks
from api.store import InMemoryStore, store
from core.state import MerchantProfile
from integrations.razorpay import RazorpayRequestError
from main import app


SECRET = "webhook-test-secret"


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
    assert state["order_id"] == "order_enriched"


def test_enrichment_failure_does_not_drop_event(monkeypatch) -> None:
    assert store.create_merchant(_merchant())

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
