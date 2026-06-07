import httpx
import pytest

from integrations.stripe import StripeClient, StripeConfigError, StripeRequestError


def test_stripe_client_loads_from_env() -> None:
    client = StripeClient.from_env({"STRIPE_API_KEY": "sk_test_123"})

    assert client.api_key == "sk_test_123"


def test_stripe_client_requires_api_key() -> None:
    with pytest.raises(StripeConfigError):
        StripeClient.from_env({})


def test_stripe_client_fetches_payment_intent_with_bearer_auth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/payment_intents/pi_123"
        assert request.headers["Authorization"] == "Bearer sk_test_123"
        return httpx.Response(200, json={"id": "pi_123", "amount": 250000})

    client = StripeClient(
        api_key="sk_test_123",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.stripe.com"),
    )

    payment_intent = client.get_payment_intent("pi_123")

    assert payment_intent == {"id": "pi_123", "amount": 250000}


def test_stripe_client_fetches_charge() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/charges/ch_123"
        return httpx.Response(200, json={"id": "ch_123", "paid": True})

    client = StripeClient(
        api_key="sk_test_123",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.stripe.com"),
    )

    charge = client.get_charge("ch_123")

    assert charge == {"id": "ch_123", "paid": True}


def test_stripe_client_raises_for_error_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    client = StripeClient(
        api_key="sk_test_123",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.stripe.com"),
    )

    with pytest.raises(StripeRequestError):
        client.get_payment_intent("pi_123")
