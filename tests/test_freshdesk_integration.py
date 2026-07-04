import base64

import httpx
import pytest

from integrations.freshdesk import FreshdeskClient, FreshdeskConfigError, FreshdeskRequestError


def test_freshdesk_client_loads_from_env() -> None:
    client = FreshdeskClient.from_env(
        {
            "FRESHDESK_API_KEY": "fd_key",
            "FRESHDESK_DOMAIN": "demo.freshdesk.com",
        }
    )

    assert client.api_key == "fd_key"
    assert client.domain == "demo.freshdesk.com"
    assert client.base_url == "https://demo.freshdesk.com/api/v2"


def test_freshdesk_client_requires_credentials() -> None:
    with pytest.raises(FreshdeskConfigError):
        FreshdeskClient.from_env({})


def test_freshdesk_client_searches_tickets_with_basic_auth() -> None:
    expected_auth = "Basic " + base64.b64encode(b"fd_key:X").decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/tickets"
        assert request.url.params["email"] == "buyer@example.com"
        assert request.headers["Authorization"] == expected_auth
        return httpx.Response(200, json=[{"id": 123, "subject": "Where is my order?"}])

    client = FreshdeskClient(
        api_key="fd_key",
        domain="demo.freshdesk.com",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://demo.freshdesk.com"),
    )

    tickets = client.search_tickets(email="buyer@example.com")

    assert tickets == [{"id": 123, "subject": "Where is my order?"}]


def test_freshdesk_client_fetches_single_ticket() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/tickets/123"
        return httpx.Response(200, json={"id": 123, "status": 5})

    client = FreshdeskClient(
        api_key="fd_key",
        domain="https://demo.freshdesk.com/",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://demo.freshdesk.com"),
    )

    ticket = client.get_ticket(123)

    assert ticket == {"id": 123, "status": 5}


def test_freshdesk_client_raises_for_error_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    client = FreshdeskClient(
        api_key="fd_key",
        domain="demo.freshdesk.com",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://demo.freshdesk.com"),
    )

    with pytest.raises(FreshdeskRequestError):
        client.search_tickets(email="buyer@example.com")
