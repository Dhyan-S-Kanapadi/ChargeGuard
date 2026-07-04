import os
from collections.abc import Mapping
from typing import Any

import httpx


class FoodPlatformConfigError(RuntimeError):
    """Raised when food platform credentials are missing."""


class FoodPlatformRequestError(RuntimeError):
    """Raised when the food platform API returns an error response."""


class FoodPlatformClient:
    """Generic food/qcom platform API client for order evidence."""

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
    def from_env(cls, env: Mapping[str, str] | None = None) -> "FoodPlatformClient":
        values = env or os.environ
        api_key = values.get("FOOD_PLATFORM_API_KEY")
        base_url = values.get("FOOD_PLATFORM_BASE_URL")

        if not api_key or not base_url:
            raise FoodPlatformConfigError(
                "FOOD_PLATFORM_API_KEY and FOOD_PLATFORM_BASE_URL are required."
            )

        return cls(api_key=api_key, base_url=base_url)

    def get_order_timeline(self, order_id: str) -> dict[str, Any]:
        response = self._get(f"/orders/{order_id}/timeline")
        if not isinstance(response, dict):
            raise FoodPlatformRequestError("Order timeline response was not an object.")
        return response

    def get_delivery_photo(self, order_id: str) -> dict[str, Any]:
        response = self._get(f"/orders/{order_id}/delivery-photo")
        if not isinstance(response, dict):
            raise FoodPlatformRequestError("Delivery photo response was not an object.")
        return response

    def _get(self, path: str) -> Any:
        if self._client is not None:
            return self._send_get(self._client, path)

        with httpx.Client(timeout=self.timeout) as client:
            return self._send_get(client, path)

    def _send_get(self, client: httpx.Client, path: str) -> Any:
        response = client.get(
            f"{self.base_url}{path}",
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        if response.status_code >= 400:
            raise FoodPlatformRequestError(
                f"Food platform request failed with {response.status_code}: {response.text}"
            )
        return response.json()
