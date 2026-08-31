import httpx
import pytest

from integrations.gmail_reader import GmailConfigError, GmailReader, GmailRequestError


def test_gmail_reader_loads_from_env() -> None:
    reader = GmailReader.from_env(
        {
            "GMAIL_ACCESS_TOKEN": "token_123",
            "GMAIL_USER_ID": "merchant@example.com",
        }
    )

    assert reader.access_token == "token_123"
    assert reader.user_id == "merchant@example.com"


def test_gmail_reader_requires_access_token() -> None:
    with pytest.raises(GmailConfigError):
        GmailReader.from_env({})


def test_gmail_reader_uses_merchant_scoped_credentials_and_user_override() -> None:
    reader = GmailReader.from_env(
        {
            "GMAIL_ACCESS_TOKEN": "global_token",
            "GMAIL_USER_ID": "global@example.com",
            "CHARGEGUARD_CONNECTOR_ACME_GMAIL_ACCESS_TOKEN": "acme_token",
            "CHARGEGUARD_CONNECTOR_ACME_GMAIL_USER_ID": "env-acme@example.com",
        },
        connector_ref="ACME",
        user_id="support@acme.example",
    )

    assert reader.access_token == "acme_token"
    assert reader.user_id == "support@acme.example"


def test_gmail_reader_searches_and_fetches_messages() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer token_123"
        if request.url.path == "/gmail/v1/users/me/messages":
            assert request.url.params["q"] == '"order_123"'
            assert request.url.params["maxResults"] == "50"
            return httpx.Response(200, json={"messages": [{"id": "msg_1"}]})

        assert request.url.path == "/gmail/v1/users/me/messages/msg_1"
        assert request.url.params["format"] == "full"
        return httpx.Response(200, json={"id": "msg_1", "threadId": "thread_1"})

    reader = GmailReader(
        access_token="token_123",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    messages = reader.search_messages('"order_123"')

    assert messages == [{"id": "msg_1", "threadId": "thread_1"}]
    assert len(requests) == 2


def test_gmail_reader_raises_for_error_response() -> None:
    reader = GmailReader(
        access_token="token_123",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(403, text="forbidden")
            )
        ),
    )

    with pytest.raises(GmailRequestError):
        reader.search_messages("order_123")
