import hashlib
import os
from collections.abc import Mapping
from typing import Any

import httpx


PAYU_BASE_URL = "https://secure.payu.in"


class PayUConfigError(RuntimeError):
    """Raised when PayU credentials are missing."""


class PayURequestError(RuntimeError):
    """Raised when PayU returns an error response."""


class PayUClient:
    """Small PayU command API client for payment evidence collection."""

    def __init__(
        self,
        *,
        merchant_key: str,
        salt: str,
        base_url: str = PAYU_BASE_URL,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.merchant_key = merchant_key
        self.salt = salt
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "PayUClient":
        values = env or os.environ
        merchant_key = values.get("PAYU_MERCHANT_KEY")
        salt = values.get("PAYU_SALT")

        if not merchant_key or not salt:
            raise PayUConfigError("PAYU_MERCHANT_KEY and PAYU_SALT are required.")

        return cls(merchant_key=merchant_key, salt=salt)

    def verify_payment(self, transaction_id: str) -> dict[str, Any]:
        return self._command("verify_payment", transaction_id)

    def _command(self, command: str, value: str) -> dict[str, Any]:
        payload = {
            "key": self.merchant_key,
            "command": command,
            "var1": value,
            "hash": self._hash(command, value),
        }
        if self._client is not None:
            return self._send_command(self._client, payload)

        with httpx.Client(timeout=self.timeout) as client:
            return self._send_command(client, payload)

    def _hash(self, command: str, value: str) -> str:
        raw = f"{self.merchant_key}|{command}|{value}|{self.salt}"
        return hashlib.sha512(raw.encode("utf-8")).hexdigest()

    def _send_command(self, client: httpx.Client, payload: dict[str, str]) -> dict[str, Any]:
        response = client.post(
            f"{self.base_url}/merchant/postservice.php?form=2",
            data=payload,
        )
        if response.status_code >= 400:
            raise PayURequestError(
                f"PayU request failed with {response.status_code}: {response.text}"
            )
        parsed = response.json()
        if not isinstance(parsed, dict):
            raise PayURequestError("PayU response was not an object.")
        return parsed
