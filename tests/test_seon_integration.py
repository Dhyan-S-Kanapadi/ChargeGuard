import json

import httpx
import pytest

from integrations.seon import SeonClient, SeonConfigError, SeonRequestError


def test_seon_client_loads_from_env() -> None:
    client = SeonClient.from_env({"SEON_API_KEY": "seon_key"})

    assert client.api_key == "seon_key"


def test_seon_client_requires_api_key() -> None:
    with pytest.raises(SeonConfigError):
        SeonClient.from_env({})


def test_seon_client_posts_fraud_check_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/SeonRestService/fraud-api/v2.0"
        assert request.headers["X-API-KEY"] == "seon_key"
        assert json.loads(request.content.decode("utf-8")) == {
            "ip": "49.36.18.22",
            "device_id": "device_demo_123",
        }
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "fraud_score": 18,
                },
            },
        )

    client = SeonClient(
        api_key="seon_key",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.seon.io"),
    )

    response = client.fraud_check(
        {
            "ip": "49.36.18.22",
            "device_id": "device_demo_123",
        }
    )

    assert response["success"] is True
    assert response["data"]["fraud_score"] == 18


def test_seon_client_raises_for_error_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    client = SeonClient(
        api_key="seon_key",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.seon.io"),
    )

    with pytest.raises(SeonRequestError) as captured:
        client.fraud_check({"ip": "49.36.18.22"})

    assert captured.value.status_code == 403
    assert "forbidden" not in str(captured.value)
