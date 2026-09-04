"""Resolve payment clients from verified merchant-owned connectors."""

from __future__ import annotations

from collections.abc import Callable
import os
from typing import Any, Protocol, cast

from core.state import MerchantProfile, PaymentConnector
from integrations.credential_secrets import (
    CredentialSecretStore,
    credential_secret_store_from_env,
)
from integrations.razorpay import RazorpayClient
from integrations.stripe import StripeClient


class PaymentConnectorRepository(Protocol):
    def get_payment_connector(
        self,
        merchant_id: str,
        connector_id: str,
    ) -> PaymentConnector | None: ...

    def list_payment_connectors(self, merchant_id: str) -> list[PaymentConnector]: ...


class PaymentConnectorError(RuntimeError):
    """A safe connector-resolution failure suitable for logs and API errors."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def global_payment_fallback_enabled() -> bool:
    return os.getenv("ALLOW_GLOBAL_PAYMENT_CREDENTIAL_FALLBACK", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


class PaymentClientFactory:
    def __init__(
        self,
        repository: PaymentConnectorRepository,
        *,
        secret_store_loader: Callable[[], CredentialSecretStore] = credential_secret_store_from_env,
        razorpay_builder: Callable[..., RazorpayClient] = RazorpayClient,
        stripe_builder: Callable[..., StripeClient] = StripeClient,
    ) -> None:
        self._repository = repository
        self._secret_store_loader = secret_store_loader
        self._razorpay_builder = razorpay_builder
        self._stripe_builder = stripe_builder

    def for_merchant(
        self,
        merchant: MerchantProfile,
        provider: str | None = None,
    ) -> RazorpayClient | StripeClient:
        merchant_id = merchant.get("merchant_id")
        selected_provider = provider or merchant.get("payment_provider")
        if not merchant_id or selected_provider not in {"razorpay", "stripe"}:
            raise PaymentConnectorError("payment_connector_not_configured")

        connector_ids = merchant.get("payment_connector_ids", {})
        connector_id = connector_ids.get(selected_provider)
        if not connector_id and merchant.get("payment_provider") == selected_provider:
            connector_id = merchant.get("payment_connector_id")
        if not connector_id:
            if any(
                item["provider"] == selected_provider
                and item["status"] != "disconnected"
                for item in self._repository.list_payment_connectors(merchant_id)
            ):
                raise PaymentConnectorError("payment_connector_not_verified")
            return self._global_fallback(selected_provider)

        connector = self._repository.get_payment_connector(merchant_id, connector_id)
        if connector is None:
            raise PaymentConnectorError("payment_connector_not_found")
        if connector["merchant_id"] != merchant_id:
            raise PaymentConnectorError("payment_connector_ownership_mismatch")
        if connector["provider"] != selected_provider:
            raise PaymentConnectorError("payment_connector_provider_mismatch")
        if connector["status"] != "verified":
            raise PaymentConnectorError("payment_connector_not_verified")

        credentials = self._secret_store_loader().get(connector_id)
        if selected_provider == "razorpay":
            if set(credentials) != {"key_id", "key_secret"}:
                raise PaymentConnectorError("payment_connector_credentials_invalid")
            return self._razorpay_builder(
                key_id=credentials["key_id"],
                key_secret=credentials["key_secret"],
            )
        if set(credentials) != {"api_key"}:
            raise PaymentConnectorError("payment_connector_credentials_invalid")
        return self._stripe_builder(api_key=credentials["api_key"])

    @staticmethod
    def _global_fallback(provider: str) -> RazorpayClient | StripeClient:
        if not global_payment_fallback_enabled():
            raise PaymentConnectorError("payment_connector_not_configured")
        if provider == "razorpay":
            return cast(Any, RazorpayClient.from_env())
        return cast(Any, StripeClient.from_env())
