"""Authenticated support connector lifecycle; credentials never enter metadata."""
from datetime import datetime, timezone
import logging
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException

from api.auth import require_api_key
from api.schemas import GmailConnectorCreate, FreshdeskConnectorCreate, SupportConnectorResponse
from api.store import store
from core.state import SupportConnector
from integrations.credential_secrets import CredentialStoreError, credential_secret_store_from_env
from integrations.freshdesk import FreshdeskRequestError
from integrations.gmail_reader import GmailRequestError
from integrations.support_client_factory import support_client, SupportProvider, SupportConnectorError

router = APIRouter(prefix="/merchants", tags=["support-connectors"], dependencies=[Depends(require_api_key)])
logger = logging.getLogger(__name__)


def verify_support_credentials(provider: SupportProvider, credentials: dict[str, str]) -> str | None:
    try:
        support_client(provider, credentials).verify_credentials()
    except (GmailRequestError, FreshdeskRequestError) as exc:
        return "provider_authentication_failed" if exc.status_code in {401, 403} else "provider_verification_failed"
    except httpx.HTTPError:
        return "provider_unavailable"
    except (ValueError, SupportConnectorError):
        return "provider_verification_failed"
    return None


def _secrets():
    try:
        return credential_secret_store_from_env()
    except CredentialStoreError as exc:
        raise HTTPException(503, exc.code) from None


def _owned(merchant_id: str, connector_id: str) -> SupportConnector:
    connector = store.get_support_connector(merchant_id, connector_id)
    if connector is None:
        raise HTTPException(404, "Support connector not found.")
    return connector


def _save(connector, *, action, expected_updated_at=None):
    try:
        return store.save_support_connector(connector, audit_action=action, expected_updated_at=expected_updated_at)
    except ValueError:
        raise HTTPException(409, "support_connector_changed") from None


def _delete_secret(secrets, connector_id):
    try:
        secrets.delete(connector_id)
    except CredentialStoreError:
        # Metadata is already disconnected; an inaccessible orphan can be retried via DELETE.
        logger.error("Unable to delete inactive support connector secret")
        raise HTTPException(503, "credential_deletion_failed") from None


def _connect(merchant_id, provider, credentials):
    if store.get_merchant(merchant_id) is None:
        raise HTTPException(404, "Merchant not found.")
    secrets = _secrets()
    error = verify_support_credentials(provider, credentials)
    now = datetime.now(timezone.utc)
    connector: SupportConnector = {
        "connector_id": f"supcon_{uuid4().hex}", "merchant_id": merchant_id,
        "provider": provider, "status": "invalid" if error else "verified",
        "verified_at": None if error else now, "created_at": now, "updated_at": now,
        "last_error_code": error,
    }
    if error:
        _save(connector, action="verification_failed")
        return connector
    try:
        secrets.put(connector["connector_id"], credentials)
    except CredentialStoreError as exc:
        raise HTTPException(503, exc.code) from None
    try:
        previous = _save(connector, action="verified")
    except Exception:
        _delete_secret(secrets, connector["connector_id"])
        raise
    if previous:
        _delete_secret(secrets, previous)
    return connector


@router.post("/{merchant_id}/support-connectors/gmail", response_model=SupportConnectorResponse, status_code=201)
def connect_gmail(merchant_id: str, payload: GmailConnectorCreate):
    return _connect(merchant_id, "gmail", {"access_token": payload.access_token.get_secret_value()})


@router.post("/{merchant_id}/support-connectors/freshdesk", response_model=SupportConnectorResponse, status_code=201)
def connect_freshdesk(merchant_id: str, payload: FreshdeskConnectorCreate):
    return _connect(merchant_id, "freshdesk", {"api_key": payload.api_key.get_secret_value(), "domain": payload.domain})


@router.get("/{merchant_id}/support-connectors", response_model=list[SupportConnectorResponse])
def list_support_connectors(merchant_id: str):
    if store.get_merchant(merchant_id) is None:
        raise HTTPException(404, "Merchant not found.")
    return store.list_support_connectors(merchant_id)


@router.post("/{merchant_id}/support-connectors/{connector_id}/verify", response_model=SupportConnectorResponse)
def verify_support_connector(merchant_id: str, connector_id: str):
    connector = _owned(merchant_id, connector_id)
    if connector["status"] == "disconnected":
        raise HTTPException(409, "support_connector_disconnected")
    secrets = _secrets()
    try:
        credentials = secrets.get(connector_id)
    except CredentialStoreError as exc:
        raise HTTPException(409, exc.code) from None
    error = verify_support_credentials(connector["provider"], credentials)
    expected = connector["updated_at"]
    connector["updated_at"] = datetime.now(timezone.utc)
    connector["last_error_code"] = error
    if error is None:
        connector["status"] = "verified"
        connector["verified_at"] = connector["updated_at"]
        action = "verified"
    elif error == "provider_authentication_failed" or connector["status"] != "verified":
        connector["status"] = "invalid"
        connector["verified_at"] = None
        action = "verification_failed"
    else:
        action = "verification_unavailable"
    previous = _save(connector, action=action, expected_updated_at=expected)
    if previous:
        _delete_secret(secrets, previous)
    return connector


@router.delete("/{merchant_id}/support-connectors/{connector_id}", response_model=SupportConnectorResponse)
def disconnect_support_connector(merchant_id: str, connector_id: str):
    connector = _owned(merchant_id, connector_id)
    expected = connector["updated_at"]
    connector.update(status="disconnected", verified_at=None, last_error_code=None, updated_at=datetime.now(timezone.utc))
    _save(connector, action="deleted", expected_updated_at=expected)
    _delete_secret(_secrets(), connector_id)
    return connector
