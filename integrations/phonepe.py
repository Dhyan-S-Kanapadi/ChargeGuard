import hashlib
import os
from collections.abc import Mapping
from typing import Any

import httpx


PHONEPE_BASE_URL = "https://api.phonepe.com/apis/hermes"


class PhonePeConfigError(RuntimeError):
    """Raised when PhonePe credentials are missing."""


class PhonePeRequestError(RuntimeError):
    """Raised when PhonePe returns an error response."""


class PhonePeClient:
    """Small PhonePe PG client for payment status evidence."""

    def __init__(
        self,
        *,
        merchant_id: str,
        salt_key: str,
        salt_index: str,
        base_url: str = PHONEPE_BASE_URL,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.merchant_id = merchant_id
        self.salt_key = salt_key
        self.salt_index = salt_index
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "PhonePeClient":
        values = env or os.environ
        merchant_id = values.get("PHONEPE_MERCHANT_ID")
        salt_key = values.get("PHONEPE_SALT_KEY")
        salt_index = values.get("PHONEPE_SALT_INDEX")

        if not merchant_id or not salt_key or not salt_index:
            raise PhonePeConfigError(
                "PHONEPE_MERCHANT_ID, PHONEPE_SALT_KEY and PHONEPE_SALT_INDEX are required."
            )

        return cls(merchant_id=merchant_id, salt_key=salt_key, salt_index=salt_index)

    def get_payment_status(self, merchant_transaction_id: str) -> dict[str, Any]:
        path = f"/pg/v1/status/{self.merchant_id}/{merchant_transaction_id}"
        return self._get(path)

    def _get(self, path: str) -> dict[str, Any]:
        if self._client is not None:
            return self._send_get(self._client, path)

        with httpx.Client(timeout=self.timeout) as client:
            return self._send_get(client, path)

    def _send_get(self, client: httpx.Client, path: str) -> dict[str, Any]:
        response = client.get(
            f"{self.base_url}{path}",
            headers={
                "X-MERCHANT-ID": self.merchant_id,
                "X-VERIFY": self._x_verify(path),
            },
        )
        if response.status_code >= 400:
            raise PhonePeRequestError(
                f"PhonePe request failed with {response.status_code}: {response.text}"
            )
        parsed = response.json()
        if not isinstance(parsed, dict):
            raise PhonePeRequestError("PhonePe response was not an object.")
        return parsed

    def _x_verify(self, path: str) -> str:
        checksum = hashlib.sha256(f"{path}{self.salt_key}".encode("utf-8")).hexdigest()
        return f"{checksum}###{self.salt_index}"
