import mimetypes
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx


RAZORPAY_BASE_URL = "https://api.razorpay.com/v1"


class RazorpayConfigError(RuntimeError):
    """Raised when Razorpay credentials are missing."""


class RazorpayRequestError(RuntimeError):
    """Raised when Razorpay returns an error or malformed response."""


class RazorpayClient:
    """Basic Auth client for Razorpay payment, dispute, and document APIs."""

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
        values = os.environ if env is None else env
        key_id = values.get("RAZORPAY_KEY_ID")
        key_secret = values.get("RAZORPAY_KEY_SECRET")
        if not key_id or not key_secret:
            raise RazorpayConfigError(
                "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are required."
            )
        return cls(key_id=key_id, key_secret=key_secret)

    def get_payment(self, payment_id: str, *, expand_card: bool = False) -> dict[str, Any]:
        params = {"expand[]": "card"} if expand_card else None
        return self._object("GET", f"/payments/{payment_id}", params=params)

    def get_order(self, order_id: str) -> dict[str, Any]:
        return self._object("GET", f"/orders/{order_id}")

    def list_disputes(
        self,
        *,
        from_timestamp: int | None = None,
        to_timestamp: int | None = None,
        count: int = 100,
        skip: int = 0,
    ) -> list[dict[str, Any]]:
        params: dict[str, int] = {"count": count, "skip": skip}
        if from_timestamp is not None:
            params["from"] = from_timestamp
        if to_timestamp is not None:
            params["to"] = to_timestamp
        response = self._object("GET", "/disputes", params=params)
        items = response.get("items")
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise RazorpayRequestError("Razorpay disputes response did not contain an item list.")
        return items

    def get_dispute(
        self,
        dispute_id: str,
        *,
        expand_payment: bool = False,
    ) -> dict[str, Any]:
        params = {"expand[]": "payment"} if expand_payment else None
        return self._object("GET", f"/disputes/{dispute_id}", params=params)

    def accept_dispute(self, dispute_id: str) -> dict[str, Any]:
        return self._object("POST", f"/disputes/{dispute_id}/accept")

    def contest_dispute(
        self,
        dispute_id: str,
        evidence: dict[str, Any],
        *,
        submit: bool = True,
    ) -> dict[str, Any]:
        payload = dict(evidence)
        payload["action"] = "submit" if submit else "draft"
        return self._object("PATCH", f"/disputes/{dispute_id}/contest", json=payload)

    def upload_document(self, file_path: str | Path) -> dict[str, Any]:
        path = Path(file_path)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as handle:
            return self._object(
                "POST",
                "/documents",
                data={"purpose": "dispute_evidence"},
                files={"file": (path.name, handle, content_type)},
            )

    def _object(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        result = self._request(method, path, **kwargs)
        if not isinstance(result, dict):
            raise RazorpayRequestError("Razorpay response was not an object.")
        return result

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if self._client is not None:
            return self._send(self._client, method, path, **kwargs)
        with httpx.Client(timeout=self.timeout) as client:
            return self._send(client, method, path, **kwargs)

    def _send(
        self,
        client: httpx.Client,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> Any:
        response = client.request(
            method,
            f"{self.base_url}{path}",
            auth=(self.key_id, self.key_secret),
            **kwargs,
        )
        if response.status_code >= 400:
            raise RazorpayRequestError(
                f"Razorpay request failed with {response.status_code} for {method} {path}."
            )
        try:
            return response.json()
        except ValueError as exc:
            raise RazorpayRequestError("Razorpay response was not valid JSON.") from exc
