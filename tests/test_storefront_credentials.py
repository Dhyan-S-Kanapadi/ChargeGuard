from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from api.store import store
from integrations.storefront_credentials import (
    verify_shopify_credential,
    verify_woocommerce_credential,
)
from main import app
from api.webhooks import build_initial_state


def _merchant_payload() -> dict:
    return {
        "merchant_id": "merchant_shopify_001",
        "name": "Shopify Merchant",
        "vertical": "ecommerce",
        "store_url": "https://shop.example",
        "storefront_platform": "shopify",
        "shopify_admin_api_token": "shpat_secret",
    }


@pytest.fixture(autouse=True)
def clear_store() -> None:
    store.clear()
    yield
    store.clear()


def test_valid_shopify_token_verifies() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Shopify-Access-Token"] == "shpat_secret"
        return httpx.Response(200, json={"shop": {"id": 1}}, request=request)

    result = verify_shopify_credential(
        "https://shop.example",
        "shpat_secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert result == {"verified": True, "reason": "shopify_credential_verified"}


def test_invalid_shopify_token_returns_clear_reason() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(401, request=request)
        )
    )

    assert verify_shopify_credential("https://shop.example", "bad", client=client) == {
        "verified": False,
        "reason": "shopify_credential_rejected",
    }


def test_storefront_credentials_are_never_sent_over_plain_http() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"shop": {"id": 1}}, request=request)

    result = verify_shopify_credential(
        "http://shop.example",
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert result == {"verified": False, "reason": "shopify_https_required"}
    assert called is False


def test_valid_woocommerce_key_and_secret_verify_with_basic_auth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"].startswith("Basic ")
        return httpx.Response(200, json={"environment": {}}, request=request)

    result = verify_woocommerce_credential(
        "https://shop.example",
        "consumer_key",
        "consumer_secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert result == {"verified": True, "reason": "woocommerce_credential_verified"}


def test_shopify_verification_persists_without_exposing_token(monkeypatch) -> None:
    monkeypatch.setenv("API_KEY", "test-api-key")
    monkeypatch.setattr(
        "api.merchants.verify_shopify_credential",
        lambda store_url, token: {"verified": True, "reason": "shopify_credential_verified"},
    )
    client = TestClient(app, headers={"X-API-Key": "test-api-key"})

    response = client.post("/merchants", json=_merchant_payload())

    assert response.status_code == 201
    assert response.json()["platform_credential_verified"] is True
    assert response.json()["platform_credential_verified_at"] is not None
    assert "shopify_admin_api_token" not in response.json()
    merchant = store.get_merchant("merchant_shopify_001")
    assert merchant is not None
    assert merchant["shopify_admin_api_token"] == "shpat_secret"
    assert merchant["platform_credential_verified"] is True


def test_credentials_are_removed_before_a_merchant_enters_dispute_state() -> None:
    merchant = _merchant_payload()
    state = build_initial_state(
        chargeback_id="cb_secret_test",
        order_id="order_1",
        payment_id="payment_1",
        reason_code="10.4",
        card_network="VISA",
        dispute_amount=100,
        currency="INR",
        filing_deadline=datetime.now(timezone.utc) + timedelta(days=30),
        merchant_profile=merchant,
    )
    assert "shopify_admin_api_token" not in state["merchant_profile"]


def test_merchant_update_reverifies_submitted_platform_credential(monkeypatch) -> None:
    monkeypatch.setenv("API_KEY", "test-api-key")
    monkeypatch.setattr(
        "api.merchants.verify_shopify_credential",
        lambda store_url, token: {"verified": True, "reason": "shopify_credential_verified"},
    )
    client = TestClient(app, headers={"X-API-Key": "test-api-key"})
    payload = _merchant_payload()
    payload.pop("shopify_admin_api_token")
    assert client.post("/merchants", json=payload).status_code == 201

    response = client.patch(
        "/merchants/merchant_shopify_001",
        json={"shopify_admin_api_token": "replacement_secret"},
    )

    assert response.status_code == 200
    assert response.json()["platform_credential_verified"] is True
    assert "shopify_admin_api_token" not in response.json()
    merchant = store.get_merchant("merchant_shopify_001")
    assert merchant is not None
    assert merchant["shopify_admin_api_token"] == "replacement_secret"
