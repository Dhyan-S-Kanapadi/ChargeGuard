import json

import httpx
import pytest

from integrations.shiprocket import ShiprocketClient, ShiprocketConfigError, ShiprocketRequestError


def test_shiprocket_client_loads_from_env() -> None:
    client = ShiprocketClient.from_env(
        {
            "SHIPROCKET_EMAIL": "merchant@example.com",
            "SHIPROCKET_PASSWORD": "password",
        }
    )

    assert client.email == "merchant@example.com"
    assert client.password == "password"


def test_shiprocket_client_requires_credentials() -> None:
    with pytest.raises(ShiprocketConfigError):
        ShiprocketClient.from_env({})


def test_shiprocket_client_authenticates_and_fetches_tracking() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/external/auth/login":
            payload = json.loads(request.content.decode("utf-8"))
            assert payload == {
                "email": "merchant@example.com",
                "password": "password",
            }
            return httpx.Response(200, json={"token": "shiprocket_token"})

        assert request.url.path == "/v1/external/courier/track/awb/awb_123"
        assert request.headers["Authorization"] == "Bearer shiprocket_token"
        return httpx.Response(200, json={"tracking_id": "awb_123", "status": "DELIVERED"})

    client = ShiprocketClient(
        email="merchant@example.com",
        password="password",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://apiv2.shiprocket.in"),
    )

    tracking = client.get_tracking("awb_123")

    assert tracking == {"tracking_id": "awb_123", "status": "DELIVERED"}
    assert len(requests) == 2


def test_shiprocket_client_raises_when_auth_token_is_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    client = ShiprocketClient(
        email="merchant@example.com",
        password="password",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://apiv2.shiprocket.in"),
    )

    with pytest.raises(ShiprocketRequestError):
        client.get_tracking("awb_123")


def test_shiprocket_client_raises_for_error_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/external/auth/login":
            return httpx.Response(200, json={"token": "shiprocket_token"})
        return httpx.Response(404, text="not found")

    client = ShiprocketClient(
        email="merchant@example.com",
        password="password",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://apiv2.shiprocket.in"),
    )

    with pytest.raises(ShiprocketRequestError):
        client.get_tracking("awb_123")
