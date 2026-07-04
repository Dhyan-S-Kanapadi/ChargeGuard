import httpx
import pytest

from integrations.delhivery import (
    DelhiveryClient,
    DelhiveryConfigError,
    DelhiveryRequestError,
)


def test_delhivery_client_loads_from_env() -> None:
    client = DelhiveryClient.from_env({"DELHIVERY_API_TOKEN": "token_123"})

    assert client.api_token == "token_123"


def test_delhivery_client_requires_token() -> None:
    with pytest.raises(DelhiveryConfigError):
        DelhiveryClient.from_env({})


def test_delhivery_client_fetches_waybill_tracking() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/packages/json/"
        assert request.url.params["waybill"] == "awb_123"
        assert request.headers["Authorization"] == "Token token_123"
        return httpx.Response(200, json={"ShipmentData": []})

    client = DelhiveryClient(
        api_token="token_123",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.get_tracking("awb_123") == {"ShipmentData": []}


def test_delhivery_client_raises_for_error_response() -> None:
    client = DelhiveryClient(
        api_token="token_123",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(401, text="unauthorized")
            )
        ),
    )

    with pytest.raises(DelhiveryRequestError):
        client.get_tracking("awb_123")
