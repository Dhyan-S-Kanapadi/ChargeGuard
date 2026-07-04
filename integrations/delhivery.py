import os
from collections.abc import Mapping
from typing import Any

import httpx


DELHIVERY_BASE_URL = "https://track.delhivery.com"


class DelhiveryConfigError(RuntimeError):
    """Raised when Delhivery credentials are missing."""


class DelhiveryRequestError(RuntimeError):
    """Raised when Delhivery returns an invalid or error response."""


class DelhiveryClient:
    """Small Delhivery tracking client for shipping evidence collection."""

    def __init__(
        self,
        *,
        api_token: str,
        base_url: str = DELHIVERY_BASE_URL,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_token = api_token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "DelhiveryClient":
        values = env or os.environ
        api_token = values.get("DELHIVERY_API_TOKEN")
        if not api_token:
            raise DelhiveryConfigError("DELHIVERY_API_TOKEN is required.")
        return cls(api_token=api_token)

    def get_tracking(self, waybill: str) -> dict[str, Any]:
        if not waybill:
            raise ValueError("waybill is required")
        if self._client is not None:
            return self._send_get(self._client, waybill)

        with httpx.Client(timeout=self.timeout) as client:
            return self._send_get(client, waybill)

    def _send_get(self, client: httpx.Client, waybill: str) -> dict[str, Any]:
        response = client.get(
            f"{self.base_url}/api/v1/packages/json/",
            params={"waybill": waybill},
            headers={"Authorization": f"Token {self.api_token}"},
        )
        if response.status_code >= 400:
            raise DelhiveryRequestError(
                f"Delhivery request failed with {response.status_code}: {response.text}"
            )
        parsed = response.json()
        if not isinstance(parsed, dict):
            raise DelhiveryRequestError("Delhivery response was not an object.")
        return parsed
