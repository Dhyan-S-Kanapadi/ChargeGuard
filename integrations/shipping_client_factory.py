"""Resolve shipping clients from verified merchant-owned connectors."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from core.state import MerchantProfile, ShippingConnector
from integrations.credential_secrets import CredentialSecretStore, credential_secret_store_from_env
from integrations.delhivery import DelhiveryClient
from integrations.shiprocket import ShiprocketClient


class ShippingConnectorRepository(Protocol):
    def get_shipping_connector(self, merchant_id: str, connector_id: str) -> ShippingConnector | None: ...

    def list_shipping_connectors(self, merchant_id: str) -> list[ShippingConnector]: ...


class ShippingConnectorError(RuntimeError):
    """Safe merchant-scoped connector-resolution failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ShippingClientFactory:
    def __init__(
        self,
        repository: ShippingConnectorRepository,
        *,
        secret_store_loader: Callable[[], CredentialSecretStore] = credential_secret_store_from_env,
        shiprocket_builder: Callable[..., ShiprocketClient] = ShiprocketClient,
        delhivery_builder: Callable[..., DelhiveryClient] = DelhiveryClient,
    ) -> None:
        self._repository = repository
        self._secret_store_loader = secret_store_loader
        self._shiprocket_builder = shiprocket_builder
        self._delhivery_builder = delhivery_builder

    def for_merchant(
        self, merchant: MerchantProfile, provider: str
    ) -> ShiprocketClient | DelhiveryClient:
        merchant_id = merchant.get("merchant_id")
        if not merchant_id or provider not in {"shiprocket", "delhivery"}:
            raise ShippingConnectorError("shipping_connector_not_configured")
        connector_id = merchant.get("shipping_connector_ids", {}).get(provider)
        if not connector_id:
            if any(item["provider"] == provider and item["status"] != "disconnected"
                   for item in self._repository.list_shipping_connectors(merchant_id)):
                raise ShippingConnectorError("shipping_connector_not_verified")
            raise ShippingConnectorError("shipping_connector_not_configured")
        connector = self._repository.get_shipping_connector(merchant_id, connector_id)
        if connector is None:
            raise ShippingConnectorError("shipping_connector_not_found")
        if connector["merchant_id"] != merchant_id:
            raise ShippingConnectorError("shipping_connector_ownership_mismatch")
        if connector["provider"] != provider:
            raise ShippingConnectorError("shipping_connector_provider_mismatch")
        if connector["status"] != "verified":
            raise ShippingConnectorError("shipping_connector_not_verified")
        credentials = self._secret_store_loader().get(connector_id)
        if provider == "shiprocket":
            if set(credentials) != {"email", "password"}:
                raise ShippingConnectorError("shipping_connector_credentials_invalid")
            return self._shiprocket_builder(email=credentials["email"], password=credentials["password"])
        if set(credentials) != {"api_token"}:
            raise ShippingConnectorError("shipping_connector_credentials_invalid")
        return self._delhivery_builder(api_token=credentials["api_token"])
