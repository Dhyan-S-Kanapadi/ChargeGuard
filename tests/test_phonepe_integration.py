import hashlib

import httpx
import pytest

from integrations.phonepe import PhonePeClient, PhonePeConfigError, PhonePeRequestError


def test_phonepe_client_loads_from_env() -> None:
    client = PhonePeClient.from_env(
        {
            "PHONEPE_MERCHANT_ID": "MID123",
            "PHONEPE_SALT_KEY": "salt",
            "PHONEPE_SALT_INDEX": "1",
        }
    )

    assert client.merchant_id == "MID123"
    assert client.salt_key == "salt"
    assert client.salt_index == "1"


def test_phonepe_client_requires_credentials() -> None:
    with pytest.raises(PhonePeConfigError):
        PhonePeClient.from_env({})


def test_phonepe_client_fetches_payment_status_with_signature() -> None:
    path = "/pg/v1/status/MID123/txn_123"
    expected_x_verify = hashlib.sha256(f"{path}salt".encode("utf-8")).hexdigest() + "###1"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/apis/hermes/pg/v1/status/MID123/txn_123"
        assert request.headers["X-MERCHANT-ID"] == "MID123"
        assert request.headers["X-VERIFY"] == expected_x_verify
        return httpx.Response(200, json={"success": True, "code": "PAYMENT_SUCCESS"})

    client = PhonePeClient(
        merchant_id="MID123",
        salt_key="salt",
        salt_index="1",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.phonepe.com"),
    )

    response = client.get_payment_status("txn_123")

    assert response == {"success": True, "code": "PAYMENT_SUCCESS"}


def test_phonepe_client_raises_for_error_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    client = PhonePeClient(
        merchant_id="MID123",
        salt_key="salt",
        salt_index="1",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.phonepe.com"),
    )

    with pytest.raises(PhonePeRequestError):
        client.get_payment_status("txn_123")
