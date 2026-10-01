"""Resolve Ethoca and Verifi clients from verified merchant-owned connectors."""

from collections.abc import Callable
from typing import Literal, Protocol

from core.state import ConsortiumConnector, MerchantProfile
from integrations.credential_secrets import CredentialSecretStore, credential_secret_store_from_env
from integrations.ethoca import EthocaClient
from integrations.verifi import VerifiClient


ConsortiumProvider = Literal["ethoca", "verifi"]


class ConsortiumConnectorRepository(Protocol):
    def get_consortium_connector(self, merchant_id: str, connector_id: str) -> ConsortiumConnector | None: ...

    def list_consortium_connectors(self, merchant_id: str) -> list[ConsortiumConnector]: ...


class ConsortiumConnectorError(RuntimeError):
    """Safe merchant-scoped connector-resolution failure."""


class ConsortiumClientFactory:
    def __init__(
        self,
        repository: ConsortiumConnectorRepository,
        *,
        secret_store_loader: Callable[[], CredentialSecretStore] = credential_secret_store_from_env,
        ethoca_builder: Callable[..., EthocaClient] = EthocaClient,
        verifi_builder: Callable[..., VerifiClient] = VerifiClient,
    ) -> None:
        self._repository = repository
        self._secret_store_loader = secret_store_loader
        self._ethoca_builder = ethoca_builder
        self._verifi_builder = verifi_builder

    def for_merchant(
        self, merchant: MerchantProfile, provider: ConsortiumProvider
    ) -> EthocaClient | VerifiClient:
        merchant_id = merchant.get("merchant_id")
        if not merchant_id or provider not in {"ethoca", "verifi"}:
            raise ConsortiumConnectorError("consortium_connector_not_configured")
        connector_id = merchant.get("consortium_connector_ids", {}).get(provider)
        if not connector_id:
            if any(item["provider"] == provider and item["status"] != "disconnected"
                   for item in self._repository.list_consortium_connectors(merchant_id)):
                raise ConsortiumConnectorError("consortium_connector_not_verified")
            raise ConsortiumConnectorError("consortium_connector_not_configured")
        connector = self._repository.get_consortium_connector(merchant_id, connector_id)
        if connector is None:
            raise ConsortiumConnectorError("consortium_connector_not_found")
        if connector["merchant_id"] != merchant_id or connector["provider"] != provider:
            raise ConsortiumConnectorError("consortium_connector_ownership_mismatch")
        if connector["status"] != "verified":
            raise ConsortiumConnectorError("consortium_connector_not_verified")
        credentials = self._secret_store_loader().get(connector_id)
        if set(credentials) != {"api_key", "base_url"}:
            raise ConsortiumConnectorError("consortium_connector_credentials_invalid")
        builder = self._ethoca_builder if provider == "ethoca" else self._verifi_builder
        return builder(api_key=credentials["api_key"], base_url=credentials["base_url"])
