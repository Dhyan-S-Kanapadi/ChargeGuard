import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from api import public_demo as demo
from api.store import store
from main import app


@pytest.fixture
def client(monkeypatch, tmp_path):
    store.clear()
    for name in ("PUBLIC_DEMO_ENABLED", "CHARGEGUARD_DEMO_SEED", "CHARGEGUARD_USE_STUBS",
                 "RAZORPAY_SIMULATOR_ENABLED", "RAZORPAY_WEBHOOK_ENABLED", "CASE_SUMMARY_USE_STUBS"):
        monkeypatch.setenv(name, "true")
    for name in ("REBUTTAL_NARRATIVE_ENABLED", "REASON_CLASSIFICATION_ENABLED"):
        monkeypatch.setenv(name, "false")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("API_KEY", "operator-private")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "webhook-private")
    monkeypatch.setenv("PUBLIC_DEMO_DB_PATH", str(tmp_path / "demo.sqlite3"))
    for provider in ("RAZORPAY", "STRIPE", "SHIPROCKET", "DELHIVERY", "SEON", "GMAIL",
                     "FRESHDESK", "ETHOCA", "VERIFI", "CLAUDE_VISION"):
        monkeypatch.setenv(f"{provider}_USE_STUBS", "true")
    yield TestClient(app)
    store.clear()


def session(client):
    response = client.post("/demo/session", headers={"X-Demo-Request": "1"})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return {"X-Demo-Session": response.json()["session_token"]}


def test_opt_in_and_configuration_guards(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_DEMO_ENABLED", "false")
    assert client.post("/demo/session").status_code == 404
    monkeypatch.setenv("PUBLIC_DEMO_ENABLED", "true")
    for name, value in [("ENVIRONMENT", "production"), ("SEON_USE_STUBS", "false"),
                        ("REBUTTAL_NARRATIVE_ENABLED", "true")]:
        with monkeypatch.context() as change:
            change.setenv(name, value)
            assert client.post("/demo/session", headers={"X-Demo-Request": "1"}).status_code == 503


def test_session_not_operator_key_and_expiry(client):
    assert client.post("/demo/session").status_code == 403
    headers = session(client)
    assert client.get("/demo/stats", headers=headers).status_code == 200
    assert client.get("/stats", headers=headers).status_code == 401
    assert client.get("/stats", headers={"X-API-Key": headers["X-Demo-Session"]}).status_code == 401
    assert client.get("/demo/stats", headers={"X-Demo-Session": "forged"}).status_code == 401
    with demo._db() as db:
        db.execute("UPDATE sessions SET expires=0")
    assert client.get("/demo/stats", headers=headers).status_code == 401


@pytest.mark.parametrize("path,method", [
    ("/merchants", "post"), ("/orders/ingest", "post"),
    ("/internal/razorpay/reconcile", "post"), ("/disputes/x/outcome", "post"),
    ("/demo/disputes/x/summary", "get"), ("/demo/disputes/x/outcome", "post"),
    ("/demo/merchants/x/payment-connectors/stripe", "post"),
    ("/demo/dev/razorpay-simulator/disputes", "post"),
    ("/demo/dev/razorpay-simulator/disputes/x/transition", "post"),
])
def test_demo_has_no_admin_or_mutation_routes(client, path, method):
    assert getattr(client, method)(path, headers=session(client)).status_code in {401, 404, 405}


def fake_run(scenario_id, payload, *, on_created):
    case_id = "disp_SIM_" + hashlib.sha256(str(len(store.list_disputes())).encode()).hexdigest()[:12]
    store.create_dispute({"chargeback_id": case_id, "merchant_profile": {"merchant_id": demo.MERCHANT_ID},
                          "dispute_amount": 100, "currency": "INR", "transaction": {"raw": {"secret": "hidden"}}})
    on_created(case_id)
    return {"scenario_id": scenario_id, "dispute_id": case_id, "order_seeded": True, "expected": "test",
            "deliveries": [{"event_id": "event", "event_name": "created", "payload_sha256": "hash",
                            "delivery": {"status_code": 202, "signature": "private-signature", "body": "private"}}]}


def test_case_and_chat_isolation_and_redaction(client, monkeypatch):
    monkeypatch.setattr(demo, "execute_scenario", fake_run)
    first, second = session(client), session(client)
    result = client.post("/demo/dev/razorpay-simulator/scenarios/device-consistent/run",
                         headers=first, json={"merchant_id": demo.MERCHANT_ID})
    assert result.status_code == 200
    assert "private" not in result.text
    case_id = result.json()["dispute_id"]
    assert client.get(f"/demo/disputes/{case_id}", headers=second).status_code == 404
    own = client.get(f"/demo/disputes/{case_id}", headers=first)
    assert own.status_code == 200
    assert "hidden" not in own.text
    assert client.get(f"/demo/disputes/{case_id}?include_raw=true", headers=first).status_code == 403
    assert client.get("/demo/disputes", headers=second).json() == []
    assert client.get("/demo/stats", headers=second).json()["total_disputes_processed"] == 0
    monkeypatch.setattr(demo, "generate_portfolio_answer", lambda q, c: str(len(c["disputes"])))
    assert client.post("/demo/assistant/query", headers=second,
                       json={"question": "Explain case", "chargeback_id": case_id}).status_code == 404
    assert client.post("/demo/assistant/query", headers=second,
                       json={"question": "Explain portfolio"}).json()["answer"] == "0"


def test_quota_atomic_shared_and_persistent(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_DEMO_DAILY_LLM_BUDGET", "1")
    monkeypatch.setattr(demo, "generate_portfolio_answer", lambda *args: "Synthetic answer")
    headers = session(client)
    def ask(_):
        return client.post("/demo/assistant/query", headers=headers,
                           json={"question": "Explain demo"}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(ask, range(2)))
    assert sorted(statuses) == [200, 429]
    another = session(client)
    assert client.post("/demo/assistant/query", headers=another,
                       json={"question": "Explain demo"}).status_code == 429


def test_session_issuance_limit_cannot_be_bypassed_with_forwarded_header(client):
    for _ in range(5):
        session(client)
    assert client.post("/demo/session", headers={"X-Demo-Request": "1", "X-Forwarded-For": "1.2.3.4"}).status_code == 429


def test_wrong_merchant_and_unknown_scenario_do_not_run(client, monkeypatch):
    monkeypatch.setattr(demo, "execute_scenario", lambda *a, **k: pytest.fail("must not execute"))
    headers = session(client)
    assert client.post("/demo/dev/razorpay-simulator/scenarios/device-consistent/run",
                       headers=headers, json={"merchant_id": "real-merchant"}).status_code == 403
    assert client.post("/demo/dev/razorpay-simulator/scenarios/missing/run",
                       headers=headers, json={"merchant_id": demo.MERCHANT_ID}).status_code == 404
