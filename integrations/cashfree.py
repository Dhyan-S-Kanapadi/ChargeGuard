import os
from collections.abc import Mapping
from typing import Any

import httpx


CASHFREE_BASE_URL = "https://api.cashfree.com/pg"
CASHFREE_API_VERSION = "2023-08-01"


class CashfreeConfigError(RuntimeError):
    """Raised when Cashfree credentials are missing."""


class CashfreeRequestError(RuntimeError):
    """Raised when Cashfree returns an error response."""


class CashfreeClient:
    """Small Cashfree Payments API client for payment evidence collection."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        base_url: str = CASHFREE_BASE_URL,
        api_version: str = CASHFREE_API_VERSION,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url.rstrip("/")
        self.api_version = api_version
        self.timeout = timeout
        self._client = client

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "CashfreeClient":
        values = env or os.environ
        client_id = values.get("CASHFREE_CLIENT_ID")
        client_secret = values.get("CASHFREE_CLIENT_SECRET")

        if not client_id or not client_secret:
            raise CashfreeConfigError("CASHFREE_CLIENT_ID and CASHFREE_CLIENT_SECRET are required.")

        return cls(client_id=client_id, client_secret=client_secret)

    def get_order(self, order_id: str) -> dict[str, Any]:
        return self._get(f"/orders/{order_id}")

    def get_order_payments(self, order_id: str) -> list[dict[str, Any]]:
        response = self._get(f"/orders/{order_id}/payments")
        if not isinstance(response, list):
            raise CashfreeRequestError("Cashfree order payments response was not a list.")
        return response

    def _get(self, path: str) -> Any:
        if self._client is not None:
            return self._send_get(self._client, path)

        with httpx.Client(timeout=self.timeout) as client:
            return self._send_get(client, path)

    def _send_get(self, client: httpx.Client, path: str) -> Any:
        response = client.get(
            f"{self.base_url}{path}",
            headers={
                "X-Client-Id": self.client_id,
                "X-Client-Secret": self.client_secret,
                "X-Api-Version": self.api_version,
            },
        )
        if response.status_code >= 400:
            raise CashfreeRequestError(
                f"Cashfree request failed with {response.status_code}: {response.text}"
            )
        return response.json()
