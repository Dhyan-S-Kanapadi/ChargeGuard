import os
from collections.abc import Mapping
from typing import Any

import httpx


class FreshdeskConfigError(RuntimeError):
    """Raised when Freshdesk credentials are missing."""


class FreshdeskRequestError(RuntimeError):
    """Raised when Freshdesk returns an error response."""


class FreshdeskClient:
    """Small Freshdesk API client for communication evidence collection."""

    def __init__(
        self,
        *,
        api_key: str,
        domain: str,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.domain = domain.replace("https://", "").replace("http://", "").rstrip("/")
        self.base_url = f"https://{self.domain}/api/v2"
        self.timeout = timeout
        self._client = client

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "FreshdeskClient":
        values = env or os.environ
        api_key = values.get("FRESHDESK_API_KEY")
        domain = values.get("FRESHDESK_DOMAIN")

        if not api_key or not domain:
            raise FreshdeskConfigError("FRESHDESK_API_KEY and FRESHDESK_DOMAIN are required.")

        return cls(api_key=api_key, domain=domain)

    def search_tickets(self, *, email: str) -> list[dict[str, Any]]:
        response = self._get("/tickets", params={"email": email})
        if not isinstance(response, list):
            raise FreshdeskRequestError("Freshdesk tickets response was not a list.")
        return response

    def get_ticket(self, ticket_id: int | str) -> dict[str, Any]:
        response = self._get(f"/tickets/{ticket_id}", params=None)
        if not isinstance(response, dict):
            raise FreshdeskRequestError("Freshdesk ticket response was not an object.")
        return response

    def _get(self, path: str, *, params: dict[str, Any] | None) -> Any:
        if self._client is not None:
            return self._send_get(self._client, path, params=params)

        with httpx.Client(timeout=self.timeout) as client:
            return self._send_get(client, path, params=params)

    def _send_get(self, client: httpx.Client, path: str, *, params: dict[str, Any] | None) -> Any:
        response = client.get(
            f"{self.base_url}{path}",
            params=params,
            auth=(self.api_key, "X"),
        )
        if response.status_code >= 400:
            raise FreshdeskRequestError(
                f"Freshdesk request failed with {response.status_code}: {response.text}"
            )
        return response.json()
