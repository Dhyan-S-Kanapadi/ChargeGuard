import os
from collections.abc import Mapping
from typing import Any

import httpx


STRIPE_BASE_URL = "https://api.stripe.com/v1"


class StripeConfigError(RuntimeError):
    """Raised when Stripe credentials are missing."""


class StripeRequestError(RuntimeError):
    """Raised when Stripe returns an error response."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


class StripeClient:
    """Small Stripe API client for payment evidence collection."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = STRIPE_BASE_URL,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "StripeClient":
        values = os.environ if env is None else env
        api_key = values.get("STRIPE_API_KEY")

        if not api_key:
            raise StripeConfigError("STRIPE_API_KEY is required.")

        return cls(api_key=api_key)

    def get_payment_intent(self, payment_intent_id: str) -> dict[str, Any]:
        return self._get(f"/payment_intents/{payment_intent_id}")

    def get_charge(self, charge_id: str) -> dict[str, Any]:
        return self._get(f"/charges/{charge_id}")

    def verify_credentials(self) -> str:
        """Return the account ID from Stripe's read-only account endpoint."""
        account = self._get("/account")
        account_id = account.get("id")
        if not isinstance(account_id, str) or not account_id:
            raise StripeRequestError("Stripe verification response was malformed.")
        return account_id

    def _get(self, path: str) -> dict[str, Any]:
        if self._client is not None:
            return self._send_get(self._client, path)

        with httpx.Client(timeout=self.timeout) as client:
            return self._send_get(client, path)

    def _send_get(self, client: httpx.Client, path: str) -> dict[str, Any]:
        response = client.get(
            f"{self.base_url}{path}",
            headers={
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        if response.status_code >= 400:
            raise StripeRequestError(
                f"Stripe request failed with {response.status_code} for GET {path}.",
                status_code=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise StripeRequestError("Stripe response was not valid JSON.") from exc
        if not isinstance(payload, dict):
            raise StripeRequestError("Stripe response was not an object.")
        return payload
