"""Owner-managed Ethoca and Verifi connector lifecycle."""

from datetime import datetime, timezone
import logging
from typing import Literal, TypedDict
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from api.auth import require_api_key
from api.schemas import (
    ConsortiumConnectorResponse,
    ConsortiumConnectorVerify,
    EthocaConnectorCreate,
    VerifiConnectorCreate,
)
from api.store import store
from core.state import ConsortiumConnector
from integrations.credential_secrets import CredentialStoreError, credential_secret_store_from_env
from integrations.ethoca import EthocaClient, EthocaRequestError
from integrations.verifi import VerifiClient, VerifiRequestError


router = APIRouter(prefix="/merchants", tags=["consortium-connectors"], dependencies=[Depends(require_api_key)])
logger = logging.getLogger(__name__)
Provider = Literal["ethoca", "verifi"]


class VerificationResult(TypedDict):
    verified: bool
    error_code: str | None


def _verification_error(exc: EthocaRequestError | VerifiRequestError) -> VerificationResult:
    if exc.status_code in {401, 403}:
        return {"verified": False, "error_code": "provider_authentication_failed"}
    return {"verified": False, "error_code": "provider_verification_failed"}


def verify_consortium_credentials(
    provider: Provider, api_key: str, base_url: str, verification_payment_id: str
) -> VerificationResult:
    try:
        client = (EthocaClient if provider == "ethoca" else VerifiClient)(
            api_key=api_key, base_url=base_url, timeout=10.0
        )
        client.search_alerts({"payment_id": verification_payment_id})
    except (EthocaRequestError, VerifiRequestError) as exc:
        return _verification_error(exc)
    except httpx.HTTPError:
        return {"verified": False, "error_code": "provider_unavailable"}
    except ValueError:
        return {"verified": False, "error_code": "provider_verification_failed"}
    return {"verified": True, "error_code": None}


def _metadata(merchant_id: str, provider: Provider) -> ConsortiumConnector:
    now = datetime.now(timezone.utc)
    return {"connector_id": f"concon_{uuid4().hex}", "merchant_id": merchant_id,
            "provider": provider, "status": "pending", "verified_at": None,
            "created_at": now, "updated_at": now, "last_error_code": None}


def _require_merchant(merchant_id: str) -> None:
    if store.get_merchant(merchant_id) is None:
        raise HTTPException(status_code=404, detail="Merchant not found.")


def _secret_store():
    try:
        return credential_secret_store_from_env()
    except CredentialStoreError as exc:
        raise HTTPException(status_code=503, detail=exc.code) from None


def _complete_connection(
    connector: ConsortiumConnector, credentials: dict[str, str], verification: VerificationResult
) -> ConsortiumConnector:
    store.create_consortium_connector(connector, audit_action="created")
    if not verification["verified"]:
        return store.update_consortium_connector_status(
            connector["merchant_id"], connector["connector_id"], status="invalid",
            last_error_code=verification["error_code"], audit_action="verification_failed",
        ) or connector
    secrets = _secret_store()
    connector["status"] = "verified"
    connector["verified_at"] = datetime.now(timezone.utc)
    connector["updated_at"] = connector["verified_at"]
    try:
        secrets.put(connector["connector_id"], credentials)
        previous_id = store.activate_consortium_connector(connector, audit_action="verified")
    except CredentialStoreError as exc:
        store.update_consortium_connector_status(
            connector["merchant_id"], connector["connector_id"], status="invalid",
            last_error_code=exc.code, audit_action="storage_failed",
        )
        raise HTTPException(status_code=503, detail=exc.code) from None
    except Exception:
        try:
            secrets.delete(connector["connector_id"])
        except CredentialStoreError:
            logger.error("Unable to remove inactive consortium connector secret")
        raise
    if previous_id and previous_id != connector["connector_id"]:
        try:
            secrets.delete(previous_id)
        except CredentialStoreError as exc:
            logger.error("Unable to remove rotated consortium connector secret", extra={"error_code": exc.code})
    return connector


def _connect(merchant_id: str, provider: Provider, api_key: str, base_url: str, payment_id: str) -> ConsortiumConnector:
    _require_merchant(merchant_id)
    connector = _metadata(merchant_id, provider)
    verification = verify_consortium_credentials(provider, api_key, base_url, payment_id)
    return _complete_connection(connector, {"api_key": api_key, "base_url": base_url}, verification)


@router.post("/{merchant_id}/consortium-connectors/ethoca", response_model=ConsortiumConnectorResponse, status_code=status.HTTP_201_CREATED)
def connect_ethoca(merchant_id: str, payload: EthocaConnectorCreate) -> ConsortiumConnector:
    return _connect(merchant_id, "ethoca", payload.api_key.get_secret_value(), payload.base_url, payload.verification_payment_id)


@router.post("/{merchant_id}/consortium-connectors/verifi", response_model=ConsortiumConnectorResponse, status_code=status.HTTP_201_CREATED)
def connect_verifi(merchant_id: str, payload: VerifiConnectorCreate) -> ConsortiumConnector:
    return _connect(merchant_id, "verifi", payload.api_key.get_secret_value(), payload.base_url, payload.verification_payment_id)


@router.get("/{merchant_id}/consortium-connectors", response_model=list[ConsortiumConnectorResponse])
def list_consortium_connectors(merchant_id: str) -> list[ConsortiumConnector]:
    _require_merchant(merchant_id)
    return store.list_consortium_connectors(merchant_id)


@router.post("/{merchant_id}/consortium-connectors/{connector_id}/verify", response_model=ConsortiumConnectorResponse)
def verify_consortium_connector(
    merchant_id: str, connector_id: str, payload: ConsortiumConnectorVerify
) -> ConsortiumConnector:
    connector = store.get_consortium_connector(merchant_id, connector_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="Consortium connector not found.")
    if connector["status"] == "disconnected":
        raise HTTPException(status_code=409, detail="consortium_connector_disconnected")
    try:
        credentials = _secret_store().get(connector_id)
    except CredentialStoreError as exc:
        raise HTTPException(status_code=409, detail=exc.code) from None
    verification = verify_consortium_credentials(
        connector["provider"], credentials.get("api_key", ""), credentials.get("base_url", ""),
        payload.verification_payment_id,
    )
    if verification["verified"]:
        connector["status"] = "verified"
        connector["last_error_code"] = None
        connector["verified_at"] = datetime.now(timezone.utc)
        connector["updated_at"] = connector["verified_at"]
        store.activate_consortium_connector(connector, audit_action="verified")
        return store.get_consortium_connector(merchant_id, connector_id) or connector
    return store.update_consortium_connector_status(
        merchant_id, connector_id,
        status="invalid" if verification["error_code"] == "provider_authentication_failed" else None,
        last_error_code=verification["error_code"], audit_action="verification_failed",
    ) or connector


@router.delete("/{merchant_id}/consortium-connectors/{connector_id}", response_model=ConsortiumConnectorResponse)
def disconnect_consortium_connector(merchant_id: str, connector_id: str) -> ConsortiumConnector:
    if store.get_consortium_connector(merchant_id, connector_id) is None:
        raise HTTPException(status_code=404, detail="Consortium connector not found.")
    updated = store.disconnect_consortium_connector(merchant_id, connector_id)
    assert updated is not None
    try:
        _secret_store().delete(connector_id)
    except CredentialStoreError as exc:
        logger.error("Unable to delete disconnected consortium connector secret", extra={"error_code": exc.code})
        raise HTTPException(status_code=503, detail=exc.code) from None
    return updated
