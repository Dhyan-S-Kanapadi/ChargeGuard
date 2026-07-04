import hashlib

import httpx
import pytest

from integrations.payu import PayUClient, PayUConfigError, PayURequestError


def test_payu_client_loads_from_env() -> None:
    client = PayUClient.from_env(
        {
            "PAYU_MERCHANT_KEY": "payu_key",
            "PAYU_SALT": "payu_salt",
        }
    )

    assert client.merchant_key == "payu_key"
    assert client.salt == "payu_salt"


def test_payu_client_requires_credentials() -> None:
    with pytest.raises(PayUConfigError):
        PayUClient.from_env({})


def test_payu_client_verifies_payment_with_command_hash() -> None:
    expected_hash = hashlib.sha512(
        b"payu_key|verify_payment|txn_123|payu_salt"
    ).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/merchant/postservice.php"
        assert request.url.params["form"] == "2"
        body = request.content.decode("utf-8")
        assert "key=payu_key" in body
        assert "command=verify_payment" in body
        assert "var1=txn_123" in body
        assert f"hash={expected_hash}" in body
        return httpx.Response(200, json={"status": 1, "transaction_details": {}})

    client = PayUClient(
        merchant_key="payu_key",
        salt="payu_salt",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://secure.payu.in"),
    )

    response = client.verify_payment("txn_123")

    assert response == {"status": 1, "transaction_details": {}}


def test_payu_client_raises_for_error_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    client = PayUClient(
        merchant_key="payu_key",
        salt="payu_salt",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://secure.payu.in"),
    )

    with pytest.raises(PayURequestError):
        client.verify_payment("txn_123")
