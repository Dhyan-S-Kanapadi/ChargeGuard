import httpx
import pytest

from integrations.cashfree import CashfreeClient, CashfreeConfigError, CashfreeRequestError


def test_cashfree_client_loads_from_env() -> None:
    client = CashfreeClient.from_env(
        {
            "CASHFREE_CLIENT_ID": "cf_client",
            "CASHFREE_CLIENT_SECRET": "cf_secret",
        }
    )

    assert client.client_id == "cf_client"
    assert client.client_secret == "cf_secret"


def test_cashfree_client_requires_credentials() -> None:
    with pytest.raises(CashfreeConfigError):
        CashfreeClient.from_env({})


def test_cashfree_client_fetches_order_with_headers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/pg/orders/order_123"
        assert request.headers["X-Client-Id"] == "cf_client"
        assert request.headers["X-Client-Secret"] == "cf_secret"
        assert request.headers["X-Api-Version"] == "2023-08-01"
        return httpx.Response(200, json={"order_id": "order_123", "order_amount": 2500})

    client = CashfreeClient(
        client_id="cf_client",
        client_secret="cf_secret",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.cashfree.com"),
    )

    order = client.get_order("order_123")

    assert order == {"order_id": "order_123", "order_amount": 2500}


def test_cashfree_client_fetches_order_payments() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/pg/orders/order_123/payments"
        return httpx.Response(200, json=[{"cf_payment_id": "pay_123", "payment_status": "SUCCESS"}])

    client = CashfreeClient(
        client_id="cf_client",
        client_secret="cf_secret",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.cashfree.com"),
    )

    payments = client.get_order_payments("order_123")

    assert payments == [{"cf_payment_id": "pay_123", "payment_status": "SUCCESS"}]


def test_cashfree_client_raises_for_error_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    client = CashfreeClient(
        client_id="cf_client",
        client_secret="cf_secret",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.cashfree.com"),
    )

    with pytest.raises(CashfreeRequestError):
        client.get_order("order_123")
