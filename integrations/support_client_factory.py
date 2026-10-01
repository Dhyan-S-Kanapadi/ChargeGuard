"""Resolve only current, verified, merchant-owned communications connectors."""
from typing import Literal
import re

from integrations.credential_secrets import credential_secret_store_from_env
from integrations.freshdesk import FreshdeskClient
from integrations.gmail_reader import GmailReader

SupportProvider = Literal["gmail", "freshdesk"]


class SupportConnectorError(RuntimeError):
    """A fixed error category, never provider content or credentials."""


def support_client(provider: SupportProvider, credentials: dict[str, str]):
    """Build existing clients without any environment credential fallback."""
    expected = {"access_token"} if provider == "gmail" else {"api_key", "domain"}
    if provider not in {"gmail", "freshdesk"} or set(credentials) != expected:
        raise SupportConnectorError("support_connector_credentials_invalid")
    if any(not isinstance(v, str) or not v or any(c.isspace() or not c.isascii() or ord(c) < 33 for c in v)
           for v in credentials.values()):
        raise SupportConnectorError("support_connector_credentials_invalid")
    if provider == "gmail":
        # The issued token identifies its mailbox; callers cannot select another user.
        return GmailReader(access_token=credentials["access_token"], user_id="me")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.freshdesk\.com", credentials["domain"]):
        raise SupportConnectorError("support_connector_credentials_invalid")
    return FreshdeskClient(api_key=credentials["api_key"], domain=credentials["domain"])


class SupportClientFactory:
    def __init__(self, repository, *, secret_store_loader=credential_secret_store_from_env):
        self._repository = repository
        self._secret_store_loader = secret_store_loader

    def for_merchant(self, merchant, provider: SupportProvider):
        merchant_id = merchant.get("merchant_id")
        if not merchant_id:
            raise SupportConnectorError("support_connector_not_configured")
        # Resolve current metadata, not a connector cached in an older workflow state.
        connectors = [c for c in self._repository.list_support_connectors(merchant_id)
                      if c["merchant_id"] == merchant_id and c["provider"] == provider
                      and c["status"] == "verified"]
        if len(connectors) != 1:
            raise SupportConnectorError("support_connector_not_verified")
        credentials = self._secret_store_loader().get(connectors[0]["connector_id"])
        return support_client(provider, credentials)
