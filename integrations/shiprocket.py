import os
from collections.abc import Mapping
from typing import Any

import httpx


SHIPROCKET_BASE_URL = "https://apiv2.shiprocket.in/v1/external"


class ShiprocketConfigError(RuntimeError):
    """Raised when Shiprocket credentials are missing."""


class ShiprocketRequestError(RuntimeError):
    """Raised when Shiprocket returns an error response."""


class ShiprocketClient:
    """Small Shiprocket API client for shipping evidence collection."""

    def __init__(
        self,
        *,
        email: str,
        password: str,
        base_url: str = SHIPROCKET_BASE_URL,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.email = email
        self.password = password
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client
        self._token: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ShiprocketClient":
        values = env or os.environ
        email = values.get("SHIPROCKET_EMAIL")
        password = values.get("SHIPROCKET_PASSWORD")

        if not email or not password:
            raise ShiprocketConfigError("SHIPROCKET_EMAIL and SHIPROCKET_PASSWORD are required.")

        return cls(email=email, password=password)

    def get_tracking(self, awb: str) -> dict[str, Any]:
        return self._get(f"/courier/track/awb/{awb}")

    def _get_token(self) -> str:
        if self._token:
            return self._token

        response = self._post(
            "/auth/login",
            json={
                "email": self.email,
                "password": self.password,
            },
            headers={},
        )
        token = response.get("token")
        if not token:
            raise ShiprocketRequestError("Shiprocket auth response did not include a token.")

        self._token = str(token)
        return self._token

    def _get(self, path: str) -> dict[str, Any]:
        token = self._get_token()
        return self._request(
            "GET",
            path,
            headers={
                "Authorization": f"Bearer {token}",
            },
        )

    def _post(self, path: str, *, json: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        return self._request("POST", path, json=json, headers=headers)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if self._client is not None:
            return self._send_request(self._client, method, path, json=json, headers=headers)

        with httpx.Client(timeout=self.timeout) as client:
            return self._send_request(client, method, path, json=json, headers=headers)

    def _send_request(
        self,
        client: httpx.Client,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None,
        headers: dict[str, str] | None,
    ) -> dict[str, Any]:
        response = client.request(
            method,
            f"{self.base_url}{path}",
            json=json,
            headers=headers,
        )
        if response.status_code >= 400:
            raise ShiprocketRequestError(
                f"Shiprocket request failed with {response.status_code}: {response.text}"
            )
        return response.json()
