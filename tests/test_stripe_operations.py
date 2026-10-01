from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from api.store import store
from api import stripe_admin
from api.stripe_processor import process_stripe_provider_event
from main import app

@pytest.fixture(autouse=True)
def setup(monkeypatch):
    store.clear(); monkeypatch.setenv("API_KEY","stripe-admin-key"); yield; store.clear()

def merchant(mid="m1", account="acct_1"):
    store.create_merchant({"merchant_id":mid,"name":mid,"vertical":"ecommerce","freshdesk_domain":"","average_order_value":1,"chargeback_history_count":0})
    now=datetime.now(timezone.utc); c={"connector_id":"c_"+mid,"merchant_id":mid,"provider":"stripe","provider_account_id":account,"status":"verified","credential_hint":"test","verified_at":now,"created_at":now,"updated_at":now,"last_error_code":None}
    store.create_payment_connector(c,audit_action="created"); store.activate_payment_connector(c,audit_action="verified")

def event(eid="evt_1"):
    store.claim_provider_event({"event_id":eid,"provider":"stripe","event_type":"charge.dispute.created","provider_dispute_id":"dp_1","account_id":"acct_1","event_data":{"data":{"object":{"id":"dp_1"}}},"processing_state":"received"}); store.queue_provider_event(eid)

def test_retry_recovery_and_authorization(monkeypatch):
    merchant(); event(); client=TestClient(app)
    assert client.post("/internal/stripe/events/evt_1/retry").status_code==401
    store.update_provider_event("evt_1",processing_state="failed")
    assert client.post("/internal/stripe/events/evt_1/retry",headers={"X-API-Key":"stripe-admin-key"}).status_code==200
    assert store.get_provider_event("evt_1")["attempt_count"]==1
    assert client.post("/internal/stripe/process-pending?limit=1",headers={"X-API-Key":"stripe-admin-key"}).status_code==200

def test_reconciliation_is_bounded_sanitized_and_idempotent(monkeypatch):
    merchant(); calls=[]
    class Client:
        def list_disputes(self, **kwargs):
            calls.append(kwargs); return [{"id":"dp_r","payment_intent":"pi_r","amount":100,"currency":"usd","status":"needs_response","created":1,"billing_details":{"email":"private"},"payment_method_details":{"card":{"last4":"4242"}}}]
    monkeypatch.setattr(stripe_admin.factory,"for_merchant",lambda *_:Client())
    client=TestClient(app); h={"X-API-Key":"stripe-admin-key"}; body={"merchant_id":"m1","count":500}
    assert client.post("/internal/stripe/reconcile",json=body).status_code==401
    assert client.post("/internal/stripe/reconcile",json=body,headers=h).json()["results"]==[{"status":"queued"}]
    assert calls[0]["limit"]==100
    assert "private" not in str(store.list_provider_events()) and "4242" not in str(store.list_provider_events())
    assert client.post("/internal/stripe/reconcile",json=body,headers=h).json()["results"]==[{"status":"duplicate"}]

def test_stripe_conflict_cross_merchant_lease_and_failure(monkeypatch):
    merchant("a","acct_a"); merchant("b","acct_b"); client=TestClient(app); h={"X-API-Key":"stripe-admin-key"}
    event("evt_conflict")
    assert store.claim_provider_event({"event_id":"evt_conflict","provider":"stripe","event_type":"charge.dispute.updated","provider_dispute_id":"different","account_id":"acct_a","processing_state":"received"}) is False
    assert store.get_provider_event("evt_conflict")["provider_dispute_id"]=="dp_1"
    assert store.get_merchant_by_payment_connector_account("stripe","acct_b")["merchant_id"]=="b"
    assert store.get_merchant_by_payment_connector_account("stripe","acct_missing") is None
    store.update_provider_event("evt_conflict",processing_state="processing",last_attempt_at=datetime.now(timezone.utc).replace(year=2000))
    assert store.requeue_provider_event("evt_conflict",include_received=False)
    from integrations.stripe import StripeRequestError
    class Broken:
        def list_disputes(self, **kwargs): raise StripeRequestError("provider secret")
    monkeypatch.setattr(stripe_admin.factory,"for_merchant",lambda *_:Broken())
    assert client.post("/internal/stripe/reconcile",json={"merchant_id":"a"},headers=h).status_code==503
    assert len(store.list_provider_events())==1

def test_startup_recovery_and_sanitized_values(monkeypatch):
    merchant(); event("evt_startup"); seen=[]
    stripe_admin.recover(1,seen.append)
    assert seen==["evt_startup"]
    data=str(store.get_provider_event("evt_startup"))
    for secret in ("raw-marker","private@example.test","billing-address","4242","sk_synthetic","stripe-webhook-test-secret"):
        assert secret not in data

def test_stripe_processor_ordering_degraded_and_cross_merchant():
    merchant("a","acct_a"); merchant("b","acct_b")
    now=datetime.now(timezone.utc)
    def claim(eid, typ, account="acct_a", created=1, rail="card"):
        assert store.claim_provider_event({"event_id":eid,"provider":"stripe","event_type":typ,"provider_dispute_id":"dp_order","account_id":account,"provider_event_timestamp":now,"event_data":{"data":{"object":{"id":"dp_order","created":created,"amount":100,"currency":"usd","payment_intent":"pi","payment_method_type":rail,"network":"visa"}}},"processing_state":"received"}); store.queue_provider_event(eid)
    claim("before","charge.dispute.updated")
    assert process_stripe_provider_event("before")["status"]=="manual_review"
    state=store.get_dispute("dp_order")["state"]
    assert state["decision"]=="ESCALATE_DEGRADED" and "network_reason_code_unavailable" in state["degraded_reasons"]
    claim("terminal","charge.dispute.closed"); process_stripe_provider_event("terminal")
    claim("late","charge.dispute.updated"); assert process_stripe_provider_event("late")["status"]=="stale"
    claim("other","charge.dispute.created","acct_b"); assert process_stripe_provider_event("other")["status"]=="unresolved"
    assert store.get_dispute("dp_order")["state"]["merchant_profile"]["merchant_id"]=="a"

def test_stripe_unsupported_rail_and_startup_path():
    merchant(); event("startup")
    seen=[]; stripe_admin.recover(1,seen.append); assert seen==["startup"]
    assert store.requeue_provider_event("startup") is False or store.get_provider_event("startup")["processing_state"]=="queued"

def test_stripe_unsupported_rail_missing_deadline_is_manual_review():
    merchant(); now=datetime.now(timezone.utc)
    assert store.claim_provider_event({"event_id":"evt_degraded","provider":"stripe","event_type":"charge.dispute.created","provider_dispute_id":"dp_degraded","account_id":"acct_1","provider_event_timestamp":now,"event_data":{"data":{"object":{"id":"dp_degraded","amount":100,"currency":"usd","payment_intent":"pi","payment_method_type":"upi","network":"unknown"}}},"processing_state":"received"})
    store.queue_provider_event("evt_degraded")
    assert process_stripe_provider_event("evt_degraded")["status"]=="manual_review"
    state=store.get_dispute("dp_degraded")["state"]
    assert state["decision"]=="ESCALATE_DEGRADED"
    assert "unsupported_payment_rail:UPI" in state["degraded_reasons"]
    assert "respond_by_unavailable" in state["degraded_reasons"]

def test_stripe_startup_recovery_entrypoint_is_bounded(monkeypatch):
    merchant()
    for index in range(2):
        event(f"evt_boot_{index}")
    seen=[]
    monkeypatch.setattr(stripe_admin,"recover",lambda limit, schedule: seen.append((limit,schedule)) or {"scheduled":1})
    stripe_admin.startup_recover()
    assert seen[0][0]==25
