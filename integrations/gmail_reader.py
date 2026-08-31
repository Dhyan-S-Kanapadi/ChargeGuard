import os
from collections.abc import Mapping
from typing import Any

import httpx

from integrations.connector_config import connector_env_value


GMAIL_BASE_URL = "https://gmail.googleapis.com/gmail/v1"


class GmailConfigError(RuntimeError):
    """Raised when Gmail OAuth credentials are missing."""


class GmailRequestError(RuntimeError):
    """Raised when Gmail returns an invalid or error response."""


class GmailReader:
    """Read-only Gmail API client for order-related evidence."""

    def __init__(
        self,
        *,
        access_token: str,
        user_id: str = "me",
        base_url: str = GMAIL_BASE_URL,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.access_token = access_token
        self.user_id = user_id
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        connector_ref: str | None = None,
        user_id: str | None = None,
    ) -> "GmailReader":
        values = os.environ if env is None else env
        try:
            access_token = connector_env_value(
                values, connector_ref, "GMAIL_ACCESS_TOKEN"
            )
            configured_user_id = connector_env_value(
                values, connector_ref, "GMAIL_USER_ID"
            )
        except ValueError as exc:
            raise GmailConfigError(str(exc)) from exc
        if not access_token:
            raise GmailConfigError("GMAIL_ACCESS_TOKEN is required.")
        return cls(
            access_token=access_token,
            user_id=user_id or configured_user_id or "me",
        )

    def search_messages(self, query: str, *, max_results: int = 50) -> list[dict[str, Any]]:
        response = self._get(
            f"/users/{self.user_id}/messages",
            params={"q": query, "maxResults": max_results},
        )
        messages = response.get("messages") or []
        if not isinstance(messages, list):
            raise GmailRequestError("Gmail messages response was not a list.")
        return [self.get_message(str(message["id"])) for message in messages if message.get("id")]

    def get_message(self, message_id: str) -> dict[str, Any]:
        return self._get(
            f"/users/{self.user_id}/messages/{message_id}",
            params={"format": "full"},
        )

    def _get(self, path: str, *, params: dict[str, Any]) -> dict[str, Any]:
        if self._client is not None:
            return self._send_get(self._client, path, params=params)
        with httpx.Client(timeout=self.timeout) as client:
            return self._send_get(client, path, params=params)

    def _send_get(
        self,
        client: httpx.Client,
        path: str,
        *,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        response = client.get(
            f"{self.base_url}{path}",
            params=params,
            headers={"Authorization": f"Bearer {self.access_token}"},
        )
        if response.status_code >= 400:
            raise GmailRequestError(
                f"Gmail request failed with {response.status_code}: {response.text}"
            )
        parsed = response.json()
        if not isinstance(parsed, dict):
            raise GmailRequestError("Gmail response was not an object.")
        return parsed
