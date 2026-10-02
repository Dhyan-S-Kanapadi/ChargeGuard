import os
import base64
from collections.abc import Mapping
from typing import Any

import httpx
from core.runtime import require_live_provider

from integrations.connector_config import connector_env_value, redact_credential_echoes


class FreshdeskConfigError(RuntimeError):
    """Raised when Freshdesk credentials are missing."""


class FreshdeskRequestError(RuntimeError):
    """Raised when Freshdesk returns an error response."""

    def __init__(self, message: str = "Provider request failed.", *, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)


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
        require_live_provider("freshdesk")
        self.api_key = api_key
        self.domain = domain.replace("https://", "").replace("http://", "").rstrip("/")
        self.base_url = f"https://{self.domain}/api/v2"
        self.timeout = timeout
        self._client = client

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        connector_ref: str | None = None,
        domain: str | None = None,
    ) -> "FreshdeskClient":
        values = os.environ if env is None else env
        try:
            api_key = connector_env_value(values, connector_ref, "FRESHDESK_API_KEY")
            configured_domain = connector_env_value(
                values, connector_ref, "FRESHDESK_DOMAIN"
            )
        except ValueError as exc:
            raise FreshdeskConfigError(str(exc)) from exc
        domain = domain or configured_domain

        if not api_key or not domain:
            raise FreshdeskConfigError("FRESHDESK_API_KEY and FRESHDESK_DOMAIN are required.")

        return cls(api_key=api_key, domain=domain)

    def verify_credentials(self) -> None:
        response = self._get("/tickets", params={"per_page": 1})
        if not isinstance(response, list) or any(not isinstance(t, dict) or not t.get("id") for t in response):
            raise FreshdeskRequestError("Invalid Freshdesk verification response.")

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
        if not 200 <= response.status_code < 300:
            raise FreshdeskRequestError(
                "Freshdesk request failed.", status_code=response.status_code
            )
        try:
            parsed = response.json()
        except ValueError:
            raise FreshdeskRequestError("Invalid Freshdesk response.") from None
        basic_token = base64.b64encode(f"{self.api_key}:X".encode()).decode()
        return redact_credential_echoes(parsed, self.api_key, basic_token)
