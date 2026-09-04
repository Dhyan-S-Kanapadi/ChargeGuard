"""Resolve SEON clients from merchant-owned device-risk connectors."""

from __future__ import annotations

from collections.abc import Callable
import os
from typing import Protocol

from core.state import DeviceRiskConnector, MerchantProfile
from integrations.credential_secrets import (
    CredentialSecretStore,
    credential_secret_store_from_env,
)
from integrations.seon import SeonClient


class DeviceRiskConnectorRepository(Protocol):
    def get_device_risk_connector(
        self, merchant_id: str, connector_id: str
    ) -> DeviceRiskConnector | None: ...

    def list_device_risk_connectors(
        self, merchant_id: str
    ) -> list[DeviceRiskConnector]: ...


class DeviceRiskConnectorError(RuntimeError):
    """A safe connector-resolution failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def global_seon_fallback_enabled() -> bool:
    return os.getenv("ALLOW_GLOBAL_SEON_CREDENTIAL_FALLBACK", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


class DeviceRiskClientFactory:
    def __init__(
        self,
        repository: DeviceRiskConnectorRepository,
        *,
        secret_store_loader: Callable[[], CredentialSecretStore] = credential_secret_store_from_env,
        seon_builder: Callable[..., SeonClient] = SeonClient,
    ) -> None:
        self._repository = repository
        self._secret_store_loader = secret_store_loader
        self._seon_builder = seon_builder

    def for_merchant(self, merchant: MerchantProfile) -> SeonClient:
        merchant_id = merchant.get("merchant_id")
        if not merchant_id:
            raise DeviceRiskConnectorError("device_risk_connector_not_configured")
        connectors = self._repository.list_device_risk_connectors(merchant_id)
        pending = next(
            (item for item in connectors if item["status"] == "verification_pending"),
            None,
        )
        connector_id = (
            pending["connector_id"]
            if pending
            else merchant.get("device_risk_connector_id")
        )
        if not connector_id:
            if any(
                item["status"] != "disconnected"
                for item in connectors
            ):
                raise DeviceRiskConnectorError("device_risk_connector_not_verified")
            if not global_seon_fallback_enabled():
                raise DeviceRiskConnectorError("device_risk_connector_not_configured")
            return SeonClient.from_env()

        connector = self._repository.get_device_risk_connector(merchant_id, connector_id)
        if connector is None:
            raise DeviceRiskConnectorError("device_risk_connector_not_found")
        if connector["merchant_id"] != merchant_id:
            raise DeviceRiskConnectorError("device_risk_connector_ownership_mismatch")
        if connector["provider"] != "seon":
            raise DeviceRiskConnectorError("device_risk_connector_provider_mismatch")
        if connector["status"] not in {"verification_pending", "verified"}:
            raise DeviceRiskConnectorError("device_risk_connector_not_verified")
        credentials = self._secret_store_loader().get(connector_id)
        if set(credentials) != {"api_key"}:
            raise DeviceRiskConnectorError("device_risk_connector_credentials_invalid")
        return self._seon_builder(api_key=credentials["api_key"])
