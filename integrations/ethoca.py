import os
from collections.abc import Mapping
from typing import Any

import httpx


class EthocaConfigError(RuntimeError):
    """Raised when Ethoca credentials are missing."""


class EthocaRequestError(RuntimeError):
    """Raised when Ethoca returns an invalid or error response."""


class EthocaClient:
    """Configurable Ethoca alert-search client for enterprise integrations."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "EthocaClient":
        values = env or os.environ
        api_key = values.get("ETHOCA_API_KEY")
        base_url = values.get("ETHOCA_BASE_URL")
        if not api_key or not base_url:
            raise EthocaConfigError("ETHOCA_API_KEY and ETHOCA_BASE_URL are required.")
        return cls(api_key=api_key, base_url=base_url)

    def search_alerts(self, identifiers: dict[str, str]) -> dict[str, Any]:
        return self._post("/alerts/search", json=identifiers)

    def _post(self, path: str, *, json: dict[str, Any]) -> dict[str, Any]:
        if self._client is not None:
            return self._send_post(self._client, path, json=json)
        with httpx.Client(timeout=self.timeout) as client:
            return self._send_post(client, path, json=json)

    def _send_post(
        self,
        client: httpx.Client,
        path: str,
        *,
        json: dict[str, Any],
    ) -> dict[str, Any]:
        response = client.post(
            f"{self.base_url}{path}",
            json=json,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        if response.status_code >= 400:
            raise EthocaRequestError(
                f"Ethoca request failed with {response.status_code}: {response.text}"
            )
        parsed = response.json()
        if not isinstance(parsed, dict):
            raise EthocaRequestError("Ethoca response was not an object.")
        return parsed
