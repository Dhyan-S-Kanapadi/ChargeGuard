import base64

import httpx
import pytest

from integrations.razorpay import RazorpayClient, RazorpayConfigError, RazorpayRequestError


def test_razorpay_client_loads_from_env() -> None:
    client = RazorpayClient.from_env(
        {
            "RAZORPAY_KEY_ID": "rzp_test_key",
            "RAZORPAY_KEY_SECRET": "secret",
        }
    )

    assert client.key_id == "rzp_test_key"
    assert client.key_secret == "secret"


def test_razorpay_client_requires_credentials() -> None:
    with pytest.raises(RazorpayConfigError):
        RazorpayClient.from_env({})


def test_razorpay_client_fetches_payment_with_basic_auth() -> None:
    expected_auth = "Basic " + base64.b64encode(b"rzp_test_key:secret").decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/payments/pay_123"
        assert request.headers["Authorization"] == expected_auth
        return httpx.Response(200, json={"id": "pay_123", "amount": 250000})

    client = RazorpayClient(
        key_id="rzp_test_key",
        key_secret="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.razorpay.com"),
    )

    payment = client.get_payment("pay_123")

    assert payment == {"id": "pay_123", "amount": 250000}


def test_razorpay_client_raises_for_error_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    client = RazorpayClient(
        key_id="rzp_test_key",
        key_secret="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.razorpay.com"),
    )

    with pytest.raises(RazorpayRequestError):
        client.get_order("order_123")
