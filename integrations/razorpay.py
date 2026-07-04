import os
from collections.abc import Mapping
from typing import Any

import httpx


RAZORPAY_BASE_URL = "https://api.razorpay.com/v1"


class RazorpayConfigError(RuntimeError):
    """Raised when Razorpay credentials are missing."""


class RazorpayRequestError(RuntimeError):
    """Raised when Razorpay returns an error response."""


class RazorpayClient:
    """Small Razorpay API client for transaction evidence collection."""

    def __init__(
        self,
        *,
        key_id: str,
        key_secret: str,
        base_url: str = RAZORPAY_BASE_URL,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.key_id = key_id
        self.key_secret = key_secret
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RazorpayClient":
        values = env or os.environ
        key_id = values.get("RAZORPAY_KEY_ID")
        key_secret = values.get("RAZORPAY_KEY_SECRET")

        if not key_id or not key_secret:
            raise RazorpayConfigError("RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are required.")

        return cls(key_id=key_id, key_secret=key_secret)

    def get_payment(self, payment_id: str) -> dict[str, Any]:
        return self._get(f"/payments/{payment_id}")

    def get_order(self, order_id: str) -> dict[str, Any]:
        return self._get(f"/orders/{order_id}")

    def _get(self, path: str) -> dict[str, Any]:
        if self._client is not None:
            return self._send_get(self._client, path)

        with httpx.Client(timeout=self.timeout) as client:
            return self._send_get(client, path)

    def _send_get(self, client: httpx.Client, path: str) -> dict[str, Any]:
        response = client.get(
            f"{self.base_url}{path}",
            auth=(self.key_id, self.key_secret),
        )
        if response.status_code >= 400:
            raise RazorpayRequestError(
                f"Razorpay request failed with {response.status_code}: {response.text}"
            )
        return response.json()
