from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from api.store import store
from core.state import MerchantProfile
from main import app


def _merchant() -> MerchantProfile:
    return {
        "merchant_id": "merchant_orders_001",
        "name": "Orders Merchant",
        "vertical": "ecommerce",
        "freshdesk_domain": "",
        "average_order_value": 0,
        "chargeback_history_count": 0,
    }


def _payload() -> dict:
    return {
        "merchant_id": "merchant_orders_001",
        "order_id": "order_001",
        "customer_email": "Buyer@Example.com",
        "customer_ip": "203.0.113.9",
        "user_agent": "Browser/1.0",
        "shipping_address": {"address1": "1 Main Street"},
        "order_date": datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat(),
    }


@pytest.fixture(autouse=True)
def clear_store() -> None:
    store.clear()
    yield
    store.clear()


def test_order_ingestion_is_authenticated_and_idempotently_upserts(monkeypatch) -> None:
    monkeypatch.setenv("API_KEY", "test-api-key")
    assert store.create_merchant(_merchant())
    unauthenticated = TestClient(app).post("/orders/ingest", json=_payload())
    client = TestClient(app, headers={"X-API-Key": "test-api-key"})
    created = client.post("/orders/ingest", json=_payload())
    payload = _payload()
    payload["user_agent"] = "Browser/2.0"
    updated = client.post("/orders/ingest", json=payload)

    assert unauthenticated.status_code == 401
    assert created.status_code == 200
    assert created.json()["status"] == "created"
    assert updated.json()["status"] == "updated"
    order = store.get_order("merchant_orders_001", "order_001")
    assert order is not None
    assert order["customer_email"] == "buyer@example.com"
    assert order["user_agent"] == "Browser/2.0"


def test_order_ingestion_rejects_missing_merchant_and_malformed_payload(monkeypatch) -> None:
    monkeypatch.setenv("API_KEY", "test-api-key")
    client = TestClient(app, headers={"X-API-Key": "test-api-key"})
    assert client.post("/orders/ingest", json=_payload()).status_code == 404
    assert store.create_merchant(_merchant())
    malformed = _payload()
    malformed["customer_ip"] = "not-an-ip"
    assert client.post("/orders/ingest", json=malformed).status_code == 422
    missing_id = _payload()
    del missing_id["merchant_id"]
    assert client.post("/orders/ingest", json=missing_id).status_code == 422


def test_order_ingestion_indexes_exact_provider_ids_and_rejects_conflicts(monkeypatch) -> None:
    monkeypatch.setenv("API_KEY", "test-api-key")
    assert store.create_merchant(_merchant())
    client = TestClient(app, headers={"X-API-Key": "test-api-key"})
    payload = _payload()
    payload.update(
        {
            "payment_provider": "razorpay",
            "provider_payment_id": "pay_exact_001",
            "provider_order_id": "order_rzp_001",
            "commerce_order_number": "#1001",
            "tracking_id": "awb_001",
            "fulfillment_id": "fulfillment_001",
        }
    )

    assert client.post("/orders/ingest", json=payload).status_code == 200
    assert store.get_order_by_provider_payment_id(
        "merchant_orders_001", "pay_exact_001"
    )["order_id"] == "order_001"
    assert store.get_order_by_provider_order_id(
        "merchant_orders_001", "order_rzp_001"
    )["order_id"] == "order_001"

    legacy_update = _payload()
    legacy_update["user_agent"] = "Legacy client update"
    assert client.post("/orders/ingest", json=legacy_update).status_code == 200
    assert store.get_order_by_provider_payment_id(
        "merchant_orders_001", "pay_exact_001"
    )["user_agent"] == "Legacy client update"

    conflicting = {**payload, "order_id": "order_002"}
    response = client.post("/orders/ingest", json=conflicting)
    assert response.status_code == 409
    assert "already mapped" in response.json()["detail"]


def test_provider_identifiers_are_isolated_by_merchant(monkeypatch) -> None:
    monkeypatch.setenv("API_KEY", "test-api-key")
    assert store.create_merchant(_merchant())
    other = _merchant()
    other["merchant_id"] = "merchant_orders_002"
    assert store.create_merchant(other)
    client = TestClient(app, headers={"X-API-Key": "test-api-key"})

    first = _payload()
    first["provider_payment_id"] = "pay_shared"
    second = {**first, "merchant_id": "merchant_orders_002", "order_id": "other_order"}
    assert client.post("/orders/ingest", json=first).status_code == 200
    assert client.post("/orders/ingest", json=second).status_code == 200
    assert store.get_order_by_provider_payment_id(
        "merchant_orders_001", "pay_shared"
    )["order_id"] == "order_001"
    assert store.get_order_by_provider_payment_id(
        "merchant_orders_002", "pay_shared"
    )["order_id"] == "other_order"


def test_order_ingestion_rejects_blank_optional_identifier(monkeypatch) -> None:
    monkeypatch.setenv("API_KEY", "test-api-key")
    assert store.create_merchant(_merchant())
    payload = _payload()
    payload["provider_order_id"] = "   "
    response = TestClient(app, headers={"X-API-Key": "test-api-key"}).post(
        "/orders/ingest", json=payload
    )
    assert response.status_code == 422
