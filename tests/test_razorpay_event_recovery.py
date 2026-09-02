from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from api import razorpay_admin
from api.razorpay_processor import process_razorpay_provider_event
from api.store import store
from main import app


SECRET = "recovery-webhook-secret"
API_KEY = "recovery-api-key"


@pytest.fixture(autouse=True)
def reset_store(monkeypatch):
    store.clear()
    razorpay_admin.reset_startup_recovery_state()
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("RAZORPAY_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("RAZORPAY_SIMULATOR_ENABLED", "true")
    monkeypatch.setenv("API_KEY", API_KEY)
    yield
    razorpay_admin.reset_startup_recovery_state()
    store.clear()


def _merchant(account_id: str = "acc_recovery") -> dict:
    return {
        "merchant_id": "merchant_recovery",
        "name": "Recovery Merchant",
        "vertical": "ecommerce",
        "payment_provider": "razorpay",
        "razorpay_account_id": account_id,
        "freshdesk_domain": "",
        "average_order_value": 1000.0,
        "chargeback_history_count": 0,
    }


def _raw_event(
    *,
    account_id: str = "acc_recovery",
    event_id: str = "disp_SIM_recovery",
    include_payment: bool = True,
    include_pii: bool = False,
) -> bytes:
    now = datetime.now(timezone.utc)
    payment = {
        "id": "pay_recovery",
        "amount": 250000,
        "currency": "INR",
        "order_id": "order_recovery",
        "method": "card",
        "card": {
            "network": "Visa",
            "last4": "1111",
            "international": False,
        },
        "notes": {
            "chargeguard_simulator": True,
            "chargeguard_card_network": "VISA",
            "chargeguard_network_reason_code": "10.4",
            "private_note": "do-not-store",
        },
        "created_at": int((now - timedelta(days=2)).timestamp()),
    }
    if include_pii:
        payment.update(
            {
                "email": "customer@example.test",
                "contact": "+919999999999",
                "vpa": "customer@upi",
            }
        )
    body = {
        "entity": "event",
        "account_id": account_id,
        "event": "payment.dispute.created",
        "payload": {
            "payment": {"entity": payment} if include_payment else None,
            "dispute": {
                "entity": {
                    "id": event_id,
                    "payment_id": "pay_recovery",
                    "amount": 250000,
                    "currency": "INR",
                    "reason_code": "unauthorised_transaction",
                    "respond_by": int((now + timedelta(days=5)).timestamp()),
                    "status": "open",
                    "phase": "chargeback",
                    "created_at": int((now - timedelta(days=1)).timestamp()),
                }
            },
        },
        "created_at": int(now.timestamp()),
    }
    return json.dumps(body, separators=(",", ":")).encode()


def _headers(raw: bytes, event_id: str) -> dict[str, str]:
    return {
        "X-Razorpay-Signature": hmac.new(
            SECRET.encode(), raw, hashlib.sha256
        ).hexdigest(),
        "x-razorpay-event-id": event_id,
    }


def _queue_without_running(monkeypatch, raw: bytes, event_id: str):
    scheduled: list[str] = []
    monkeypatch.setattr(
        "api.razorpay_webhooks.enqueue_razorpay_provider_event",
        lambda background_tasks, queued_event_id: scheduled.append(queued_event_id),
    )
    response = TestClient(app).post(
        "/webhook/razorpay",
        content=raw,
        headers=_headers(raw, event_id),
    )
    return response, scheduled


def test_request_path_persists_before_enqueue_and_never_calls_razorpay(
    monkeypatch,
) -> None:
    raw = _raw_event()
    observed_states: list[str] = []

    def observe_enqueue(background_tasks, event_id):
        observed_states.append(store.get_provider_event(event_id)["processing_state"])

    monkeypatch.setattr(
        "api.razorpay_webhooks.enqueue_razorpay_provider_event",
        observe_enqueue,
    )
    monkeypatch.setattr(
        "api.razorpay_service.RazorpayClient.from_env",
        lambda: pytest.fail("Razorpay REST must not run in the request path"),
    )

    response = TestClient(app).post(
        "/webhook/razorpay",
        content=raw,
        headers=_headers(raw, "evt_fast_ack"),
    )

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert observed_states == ["queued"]
    event = store.get_provider_event("evt_fast_ack")
    assert event["payload_hash"] == hashlib.sha256(raw).hexdigest()
    assert event["processed_at"] is None


def test_persisted_event_data_is_allowlisted_and_event_api_hides_it(monkeypatch) -> None:
    raw = _raw_event(include_pii=True)
    response, _ = _queue_without_running(monkeypatch, raw, "evt_pii")
    assert response.status_code == 202

    stored = store.get_provider_event("evt_pii")
    serialized = json.dumps(stored["event_data"])
    assert "customer@example.test" not in serialized
    assert "+919999999999" not in serialized
    assert "customer@upi" not in serialized
    assert "do-not-store" not in serialized
    assert "last4" not in serialized

    event_response = TestClient(app).get(
        "/internal/razorpay/events",
        headers={"X-API-Key": API_KEY},
    )
    assert event_response.status_code == 200
    assert "event_data" not in event_response.json()[0]


def test_signed_structurally_invalid_payload_returns_422() -> None:
    raw = b'{"entity":"event","account_id":"acc_recovery","event":"payment.dispute.created"}'
    response = TestClient(app).post(
        "/webhook/razorpay",
        content=raw,
        headers=_headers(raw, "evt_invalid_shape"),
    )
    assert response.status_code == 422
    assert store.get_provider_event("evt_invalid_shape") is None


def test_processor_performs_enrichment_after_ack(monkeypatch) -> None:
    assert store.create_merchant(_merchant())
    response, _ = _queue_without_running(
        monkeypatch,
        _raw_event(include_payment=False),
        "evt_enrich",
    )
    assert response.status_code == 202
    calls = []

    class FakeClient:
        def get_payment(self, payment_id, *, expand_card=False):
            calls.append((payment_id, expand_card))
            return {
                "id": payment_id,
                "order_id": "order_enriched",
                "method": "card",
                "card": {"network": "Visa"},
            }

    result = process_razorpay_provider_event(
        "evt_enrich",
        client_factory=lambda: FakeClient(),
        schedule_graph=lambda state: None,
    )

    assert calls == [("pay_recovery", True)]
    assert result["status"] == "manual_review"
    event = store.get_provider_event("evt_enrich")
    assert event["processing_state"] == "manual_review"
    assert event["processed_at"] is not None


def test_processor_schedules_created_graph_only_once(monkeypatch) -> None:
    assert store.create_merchant(_merchant())
    response, _ = _queue_without_running(monkeypatch, _raw_event(), "evt_once")
    assert response.status_code == 202
    calls = []

    first = process_razorpay_provider_event(
        "evt_once",
        schedule_graph=lambda state: calls.append(state["chargeback_id"]),
    )
    second = process_razorpay_provider_event(
        "evt_once",
        schedule_graph=lambda state: calls.append("duplicate"),
    )

    assert first["status"] == "scheduled"
    assert second["status"] == "skipped"
    assert calls == ["disp_SIM_recovery"]


def test_failed_created_event_resumes_its_unfinished_graph(monkeypatch) -> None:
    assert store.create_merchant(_merchant())
    response, _ = _queue_without_running(monkeypatch, _raw_event(), "evt_resume")
    assert response.status_code == 202

    first = process_razorpay_provider_event(
        "evt_resume",
        schedule_graph=lambda state: (_ for _ in ()).throw(
            RuntimeError("graph interrupted")
        ),
    )
    assert first["status"] == "failed"
    assert store.get_dispute("disp_SIM_recovery")["status"] == "received"
    assert store.requeue_provider_event("evt_resume", include_received=False)
    resumed = []

    second = process_razorpay_provider_event(
        "evt_resume",
        schedule_graph=lambda state: resumed.append(state["chargeback_id"]),
    )

    assert second["status"] == "scheduled"
    assert resumed == ["disp_SIM_recovery"]
    assert store.get_provider_event("evt_resume")["attempt_count"] == 2


def test_real_graph_failure_marks_provider_event_failed_and_retry_resumes(
    monkeypatch,
) -> None:
    assert store.create_merchant(_merchant())
    response, _ = _queue_without_running(
        monkeypatch,
        _raw_event(),
        "evt_graph_failure",
    )
    assert response.status_code == 202
    calls = []

    def fail_graph(state):
        calls.append("failed")
        raise RuntimeError("customer@example.test secret-value")

    monkeypatch.setattr("api.webhooks.chargeback_graph.invoke", fail_graph)
    first = process_razorpay_provider_event("evt_graph_failure")

    assert first["status"] == "failed"
    event = store.get_provider_event("evt_graph_failure")
    dispute = store.get_dispute("disp_SIM_recovery")
    assert event["processing_state"] == "failed"
    assert event["processed_at"] is not None
    assert dispute["status"] == "failed"
    assert "customer@example.test" not in event["failure_reason"]
    assert "secret-value" not in event["failure_reason"]
    assert "customer@example.test" not in dispute["error"]
    assert "secret-value" not in dispute["error"]

    def complete_graph(state):
        calls.append("completed")
        return state

    monkeypatch.setattr("api.webhooks.chargeback_graph.invoke", complete_graph)
    client = TestClient(app)
    retry = client.post(
        "/internal/razorpay/events/evt_graph_failure/retry",
        headers={"X-API-Key": API_KEY},
    )

    assert retry.status_code == 200
    assert store.get_provider_event("evt_graph_failure")["processing_state"] == "scheduled"
    assert store.get_provider_event("evt_graph_failure")["attempt_count"] == 2
    assert store.get_dispute("disp_SIM_recovery")["status"] == "completed"
    assert calls == ["failed", "completed"]

    terminal_retry = client.post(
        "/internal/razorpay/events/evt_graph_failure/retry",
        headers={"X-API-Key": API_KEY},
    )
    assert terminal_retry.status_code == 409
    assert calls == ["failed", "completed"]


def test_processing_failure_is_terminal_and_sanitized(monkeypatch) -> None:
    response, _ = _queue_without_running(monkeypatch, _raw_event(), "evt_failure")
    assert response.status_code == 202
    monkeypatch.setattr(
        "api.razorpay_processor.parse_stored_envelope",
        lambda data: (_ for _ in ()).throw(
            RuntimeError("customer@example.test secret-value")
        ),
    )

    result = process_razorpay_provider_event("evt_failure")

    assert result["status"] == "failed"
    event = store.get_provider_event("evt_failure")
    assert event["processing_state"] == "failed"
    assert event["processed_at"] is not None
    assert "customer@example.test" not in event["failure_reason"]
    assert "secret-value" not in event["failure_reason"]
    assert event["attempt_count"] == 1


def test_failed_event_can_be_retried_through_protected_endpoint(monkeypatch) -> None:
    assert store.create_merchant(_merchant())
    response, _ = _queue_without_running(monkeypatch, _raw_event(), "evt_retry")
    assert response.status_code == 202
    store.update_provider_event("evt_retry", processing_state="failed")
    client = TestClient(app)

    assert client.post("/internal/razorpay/events/evt_retry/retry").status_code == 401
    retry = client.post(
        "/internal/razorpay/events/evt_retry/retry",
        headers={"X-API-Key": API_KEY},
    )

    assert retry.status_code == 200
    assert retry.json()["status"] == "queued"
    assert store.get_provider_event("evt_retry")["processing_state"] == "scheduled"


def test_completed_or_ignored_event_cannot_be_retried() -> None:
    for event_id, state in (("evt_done", "scheduled"), ("evt_ignored", "ignored")):
        assert store.claim_provider_event(
            {
                "event_id": event_id,
                "provider": "razorpay",
                "event_type": "payment.dispute.created",
                "processing_state": "received",
            }
        )
        store.update_provider_event(event_id, processing_state=state)

    client = TestClient(app)
    headers = {"X-API-Key": API_KEY}
    assert client.post(
        "/internal/razorpay/events/evt_done/retry", headers=headers
    ).status_code == 409
    assert client.post(
        "/internal/razorpay/events/evt_ignored/retry", headers=headers
    ).status_code == 409
    assert store.get_provider_event("evt_done")["processed_at"] is not None
    assert store.get_provider_event("evt_ignored")["processed_at"] is not None


def test_stale_processing_event_can_be_reclaimed(monkeypatch) -> None:
    monkeypatch.setenv("PROVIDER_EVENT_CLAIM_TIMEOUT_SECONDS", "1")
    assert store.claim_provider_event(
        {
            "event_id": "evt_stale_processing",
            "provider": "razorpay",
            "event_type": "payment.dispute.created",
            "processing_state": "received",
        }
    )
    assert store.queue_provider_event("evt_stale_processing")
    assert store.start_provider_event_processing("evt_stale_processing")
    store.update_provider_event(
        "evt_stale_processing",
        last_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=2),
    )

    assert store.requeue_provider_event(
        "evt_stale_processing", include_received=False
    )
    event = store.get_provider_event("evt_stale_processing")
    assert event["processing_state"] == "queued"
    assert event["processed_at"] is None


def test_unresolved_event_processes_after_merchant_mapping(monkeypatch) -> None:
    response, _ = _queue_without_running(monkeypatch, _raw_event(), "evt_unresolved")
    assert response.status_code == 202
    assert process_razorpay_provider_event("evt_unresolved")["status"] == "unresolved"
    assert store.create_merchant(_merchant())

    retry = TestClient(app).post(
        "/internal/razorpay/events/evt_unresolved/retry",
        headers={"X-API-Key": API_KEY},
    )

    assert retry.status_code == 200
    assert store.get_provider_event("evt_unresolved")["processing_state"] == "scheduled"
    assert store.get_dispute("disp_SIM_recovery") is not None


def test_process_pending_is_protected_and_bounded(monkeypatch) -> None:
    scheduled = []
    monkeypatch.setattr(
        "api.razorpay_admin._enqueue_event",
        lambda background_tasks, event_id: scheduled.append(event_id),
    )
    for index in range(5):
        assert store.claim_provider_event(
            {
                "event_id": f"evt_pending_{index}",
                "provider": "razorpay",
                "event_type": "payment.dispute.created",
                "processing_state": "received",
            }
        )
        assert store.queue_provider_event(f"evt_pending_{index}")

    client = TestClient(app)
    assert client.post("/internal/razorpay/process-pending?limit=2").status_code == 401
    response = client.post(
        "/internal/razorpay/process-pending?limit=2",
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 200
    assert response.json() == {
        "considered": 2,
        "scheduled": 2,
        "skipped": 0,
        "failed": 0,
    }
    assert len(scheduled) == 2
    assert client.post(
        "/internal/razorpay/process-pending?limit=101",
        headers={"X-API-Key": API_KEY},
    ).status_code == 422


def test_startup_recovery_is_enabled_once_and_limit_is_bounded(monkeypatch) -> None:
    started = []

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            started.append(
                {
                    "target": target,
                    "args": args,
                    "name": name,
                    "daemon": daemon,
                }
            )

        def start(self):
            started[-1]["started"] = True

    monkeypatch.setenv("RAZORPAY_RECOVER_PENDING_ON_STARTUP", "true")
    monkeypatch.setenv("RAZORPAY_STARTUP_RECOVERY_LIMIT", "1000")
    monkeypatch.setattr(razorpay_admin, "Thread", FakeThread)

    assert razorpay_admin.schedule_startup_razorpay_recovery() is True
    assert razorpay_admin.schedule_startup_razorpay_recovery() is False
    assert len(started) == 1
    assert started[0]["args"] == (100,)
    assert started[0]["name"] == "razorpay-startup-recovery"
    assert started[0]["daemon"] is True
    assert started[0]["started"] is True


def test_startup_recovery_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setenv("RAZORPAY_RECOVER_PENDING_ON_STARTUP", "false")
    monkeypatch.setattr(
        razorpay_admin,
        "Thread",
        lambda **kwargs: pytest.fail("disabled recovery must not create a thread"),
    )

    assert razorpay_admin.schedule_startup_razorpay_recovery() is False


def test_startup_recovery_uses_lower_bound_and_skips_unmapped_unresolved(
    monkeypatch,
) -> None:
    monkeypatch.setenv("RAZORPAY_STARTUP_RECOVERY_LIMIT", "0")
    assert razorpay_admin._startup_recovery_limit() == 1
    assert store.claim_provider_event(
        {
            "event_id": "evt_startup_unresolved",
            "provider": "razorpay",
            "event_type": "payment.dispute.created",
            "account_id": "acc_not_mapped",
            "processing_state": "unresolved",
        }
    )
    scheduled = []

    result = razorpay_admin.recover_pending_razorpay_events(
        limit=25,
        schedule_event=scheduled.append,
    )

    assert result == {
        "considered": 1,
        "scheduled": 0,
        "skipped": 1,
        "failed": 0,
    }
    assert scheduled == []
    assert store.get_provider_event("evt_startup_unresolved")["processing_state"] == (
        "unresolved"
    )
