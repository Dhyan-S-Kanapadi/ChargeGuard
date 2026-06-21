import httpx
import pytest

from integrations.ethoca import EthocaClient, EthocaConfigError, EthocaRequestError


def test_ethoca_client_loads_from_env() -> None:
    client = EthocaClient.from_env(
        {"ETHOCA_API_KEY": "key_123", "ETHOCA_BASE_URL": "https://ethoca.test"}
    )

    assert client.api_key == "key_123"
    assert client.base_url == "https://ethoca.test"


def test_ethoca_client_requires_configuration() -> None:
    with pytest.raises(EthocaConfigError):
        EthocaClient.from_env({})


def test_ethoca_client_posts_alert_search() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/alerts/search"
        assert request.headers["Authorization"] == "Bearer key_123"
        return httpx.Response(200, json={"match": True})

    client = EthocaClient(
        api_key="key_123",
        base_url="https://ethoca.test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.search_alerts({"payment_id": "pay_123"}) == {"match": True}


def test_ethoca_client_raises_for_error_response() -> None:
    client = EthocaClient(
        api_key="key_123",
        base_url="https://ethoca.test",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(401, text="unauthorized")
            )
        ),
    )

    with pytest.raises(EthocaRequestError):
        client.search_alerts({"payment_id": "pay_123"})
