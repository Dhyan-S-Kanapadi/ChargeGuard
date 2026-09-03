import httpx
import pytest
from fastapi.testclient import TestClient

from api.store import store
from core.state import MerchantProfile, OrderRecord
from integrations.shopify import sync_shopify_history
from main import app


def _merchant(*, verified: bool = True) -> MerchantProfile:
    return {
        "merchant_id": "merchant_sync_001",
        "name": "Sync Merchant",
        "vertical": "ecommerce",
        "freshdesk_domain": "",
        "average_order_value": 0,
        "chargeback_history_count": 0,
        "store_url": "https://shop.example",
        "storefront_platform": "shopify",
        "shopify_admin_api_token": "secret",
        "platform_credential_verified": verified,
    }


def _order(order_id: int) -> dict:
    return {
        "id": order_id,
        "email": "buyer@example.com",
        "created_at": "2025-01-01T10:00:00Z",
        "client_details": {"browser_ip": "203.0.113.8", "user_agent": "Browser"},
        "shipping_address": {"address1": "1 Main Street"},
    }


@pytest.fixture(autouse=True)
def clear_store() -> None:
    store.clear()
    yield
    store.clear()


def test_shopify_sync_endpoint_rejects_unverified_credential(monkeypatch) -> None:
    monkeypatch.setenv("API_KEY", "test-api-key")
    assert store.create_merchant(_merchant(verified=False))
    response = TestClient(app, headers={"X-API-Key": "test-api-key"}).post(
        "/merchants/merchant_sync_001/sync-shopify-history"
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Shopify credential has not been verified."


def test_shopify_sync_handles_pagination_and_skips_one_bad_page() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        page = request.url.params.get("page_info")
        if page is None:
            return httpx.Response(
                200,
                json={"orders": [_order(1)]},
                headers={"Link": '<https://shop.example/orders?page_info=bad>; rel="next"'},
                request=request,
            )
        if page == "bad":
            return httpx.Response(
                500,
                headers={"Link": '<https://shop.example/orders?page_info=last>; rel="next"'},
                request=request,
            )
        return httpx.Response(200, json={"orders": [_order(2)]}, request=request)

    saved: list[OrderRecord] = []
    result = sync_shopify_history(
        _merchant(),
        lambda order: saved.append(order) is None,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )

    assert len(requests) == 3
    assert [order["order_id"] for order in saved] == ["1", "2"]
    assert result == {"created": 2, "updated": 0, "failed_pages": 1}


def test_shopify_sync_retries_after_429() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, request=request)
        return httpx.Response(200, json={"orders": []}, request=request)

    result = sync_shopify_history(
        _merchant(),
        lambda order: True,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleeps.append,
    )

    assert result["failed_pages"] == 0
    assert calls == 2
    assert sleeps == [2.0]


def test_shopify_sync_never_sends_token_to_cross_origin_pagination_link() -> None:
    requested_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        return httpx.Response(
            200,
            json={"orders": []},
            headers={"Link": '<https://attacker.example/orders?page_info=next>; rel="next"'},
            request=request,
        )

    sync_shopify_history(
        _merchant(),
        lambda order: True,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )

    assert requested_hosts == ["shop.example"]
