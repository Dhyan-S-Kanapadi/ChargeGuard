import os
from collections.abc import Mapping
from typing import Any

import httpx


SEON_BASE_URL = "https://api.seon.io/SeonRestService"


class SeonConfigError(RuntimeError):
    """Raised when SEON credentials are missing."""


class SeonRequestError(RuntimeError):
    """Raised when SEON returns an error response."""

    def __init__(self, code: str, *, status_code: int | None = None) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class SeonClient:
    """Small SEON API client for device and fraud-risk evidence."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = SEON_BASE_URL,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "SeonClient":
        values = env or os.environ
        api_key = values.get("SEON_API_KEY")

        if not api_key:
            raise SeonConfigError("SEON_API_KEY is required.")

        return cls(api_key=api_key)

    def fraud_check(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._post("/fraud-api/v2.0", json=payload)
        if not isinstance(response, dict):
            raise SeonRequestError("SEON fraud response was not an object.")
        return response

    def _post(self, path: str, *, json: dict[str, Any]) -> Any:
        if self._client is not None:
            return self._send_post(self._client, path, json=json)

        with httpx.Client(timeout=self.timeout) as client:
            return self._send_post(client, path, json=json)

    def _send_post(self, client: httpx.Client, path: str, *, json: dict[str, Any]) -> Any:
        response = client.post(
            f"{self.base_url}{path}",
            json=json,
            headers={
                "X-API-KEY": self.api_key,
            },
        )
        if response.status_code >= 400:
            raise SeonRequestError(
                "seon_request_failed",
                status_code=response.status_code,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise SeonRequestError(
                "seon_response_invalid",
                status_code=response.status_code,
            ) from exc
