from datetime import datetime, timezone
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from api.store import store
from main import app

SECRET = "stripe-webhook-test-secret"

@pytest.fixture(autouse=True)
def reset(monkeypatch):
    store.clear(); monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET); yield; store.clear()

def raw(event_id="evt_stripe_1", created=None):
    return json.dumps({"id":event_id,"type":"charge.dispute.created","account":"acct_stripe", "created":created or int(datetime.now(timezone.utc).timestamp()), "data":{"object":{"id":"dp_stripe_1","payment_intent":"pi_1","amount":1000,"currency":"usd","reason":"fraudulent","status":"needs_response","created":created or int(datetime.now(timezone.utc).timestamp()),"payment_method_details":{"type":"card","card":{"network":"visa","last4":"4242"}},"billing_details":{"email":"private@example.test"}}}}, separators=(",", ":")).encode()

def headers(body, timestamp=None):
    timestamp = timestamp or int(datetime.now(timezone.utc).timestamp())
    signature = hmac.new(SECRET.encode(), str(timestamp).encode()+b"."+body, hashlib.sha256).hexdigest()
    return {"Stripe-Signature":f"t={timestamp},v1={signature}"}

def merchant():
    store.create_merchant({"merchant_id":"merchant-stripe","name":"Stripe","vertical":"ecommerce","freshdesk_domain":"","average_order_value":1.0,"chargeback_history_count":0})
    now=datetime.now(timezone.utc); connector={"connector_id":"paycon_stripe","merchant_id":"merchant-stripe","provider":"stripe","provider_account_id":"acct_stripe","status":"verified","credential_hint":"test","verified_at":now,"created_at":now,"updated_at":now,"last_error_code":None}
    store.create_payment_connector(connector, audit_action="created"); store.activate_payment_connector(connector, audit_action="verified")

def test_signed_event_is_sanitized_and_idempotent():
    merchant(); body=raw(); client=TestClient(app)
    assert client.post("/webhook/stripe", content=body, headers=headers(body)).status_code == 202
    assert client.post("/webhook/stripe", content=body, headers=headers(body)).json()["status"] == "duplicate"
    saved=store.get_provider_event("evt_stripe_1")
    assert saved["provider"] == "stripe" and "private@example.test" not in json.dumps(saved["event_data"])
    assert "4242" not in json.dumps(saved["event_data"])

def test_signature_precedes_parsing_and_rejects_expired():
    client=TestClient(app)
    assert client.post("/webhook/stripe", content=b"not-json", headers={"Stripe-Signature":"bad"}).status_code == 401
    body=raw(); assert client.post("/webhook/stripe", content=body, headers=headers(body, 1)).status_code == 401
    assert store.list_provider_events() == []

@pytest.mark.parametrize("header", [None, "", "v1=x", "t=abc,v1=x", "t=1", "t=1,v1="])
def test_malformed_or_missing_signature_is_rejected_before_parsing(header):
    response=TestClient(app).post("/webhook/stripe",content=b"not-json",headers={} if header is None else {"Stripe-Signature":header})
    assert response.status_code==401 and store.list_provider_events()==[]

def test_multiple_v1_accepts_valid_candidate_and_oversize_is_rejected(monkeypatch):
    body=raw(); timestamp=int(datetime.now(timezone.utc).timestamp()); valid=headers(body,timestamp)["Stripe-Signature"].split("v1=")[1]
    assert TestClient(app).post("/webhook/stripe",content=body,headers={"Stripe-Signature":f"t={timestamp},v1=bad,v1={valid}"}).status_code==202
    monkeypatch.setenv("STRIPE_WEBHOOK_MAX_BODY_BYTES","1024")
    padded=body+b"x"*2000
    assert TestClient(app).post("/webhook/stripe",content=padded,headers=headers(padded)).status_code==413

def test_unsupported_event_is_ignored_and_unknown_account_is_unresolved():
    body=raw(); payload=json.loads(body); payload["type"]="charge.dispute.future"; body=json.dumps(payload).encode()
    assert TestClient(app).post("/webhook/stripe",content=body,headers=headers(body)).json()["status"]=="ignored"
    body=raw("evt_unknown"); assert TestClient(app).post("/webhook/stripe",content=body,headers=headers(body)).status_code==202
    assert store.get_provider_event("evt_unknown")["processing_state"] in {"unresolved","queued"}
