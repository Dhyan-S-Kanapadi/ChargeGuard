import httpx
import pytest

from integrations.verifi import VerifiClient, VerifiConfigError, VerifiRequestError


def test_verifi_client_loads_from_env() -> None:
    client = VerifiClient.from_env(
        {"VERIFI_API_KEY": "key_123", "VERIFI_BASE_URL": "https://verifi.test"}
    )

    assert client.api_key == "key_123"
    assert client.base_url == "https://verifi.test"


def test_verifi_client_requires_configuration() -> None:
    with pytest.raises(VerifiConfigError):
        VerifiClient.from_env({})


def test_verifi_client_posts_cdrn_alert_search() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/cdrn/alerts/search"
        assert request.headers["Authorization"] == "Bearer key_123"
        return httpx.Response(200, json={"matched": False})

    client = VerifiClient(
        api_key="key_123",
        base_url="https://verifi.test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.search_alerts({"payment_id": "pay_123"}) == {"matched": False}


def test_verifi_client_raises_for_error_response() -> None:
    client = VerifiClient(
        api_key="key_123",
        base_url="https://verifi.test",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(503, text="unavailable")
            )
        ),
    )

    with pytest.raises(VerifiRequestError):
        client.search_alerts({"payment_id": "pay_123"})
