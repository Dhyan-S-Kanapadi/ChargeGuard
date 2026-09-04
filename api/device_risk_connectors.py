"""Authenticated operator APIs for merchant-scoped SEON credentials."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from api.auth import require_api_key
from api.schemas import DeviceRiskConnectorResponse, SeonConnectorCreate
from api.store import store
from core.state import DeviceRiskConnector
from integrations.credential_secrets import (
    CredentialSecretStore,
    CredentialStoreError,
    credential_secret_store_from_env,
)


router = APIRouter(
    prefix="/merchants",
    tags=["device-risk-connectors"],
    dependencies=[Depends(require_api_key)],
)
logger = logging.getLogger(__name__)


def _require_merchant(merchant_id: str) -> None:
    if store.get_merchant(merchant_id) is None:
        raise HTTPException(status_code=404, detail="Merchant not found.")


def _secret_store() -> CredentialSecretStore:
    try:
        return credential_secret_store_from_env()
    except CredentialStoreError as exc:
        raise HTTPException(status_code=503, detail=exc.code) from exc


def _metadata(merchant_id: str, api_key: str) -> DeviceRiskConnector:
    now = datetime.now(timezone.utc)
    return {
        "connector_id": f"devcon_{uuid4().hex}",
        "merchant_id": merchant_id,
        "provider": "seon",
        "status": "verification_pending",
        "credential_hint": f"ending in {api_key[-4:]}",
        "verified_at": None,
        "last_success_at": None,
        "last_error_code": "verification_requires_first_real_request",
        "created_at": now,
        "updated_at": now,
    }


@router.post(
    "/{merchant_id}/device-risk-connectors/seon",
    response_model=DeviceRiskConnectorResponse,
    status_code=status.HTTP_201_CREATED,
)
def connect_seon(merchant_id: str, payload: SeonConnectorCreate) -> DeviceRiskConnector:
    _require_merchant(merchant_id)
    connector = _metadata(merchant_id, payload.api_key)
    secrets = _secret_store()
    superseded = [
        item["connector_id"]
        for item in store.list_device_risk_connectors(merchant_id)
        if item["status"] == "verification_pending"
    ]
    try:
        secrets.put(connector["connector_id"], {"api_key": payload.api_key})
        if not store.configure_device_risk_connector(connector):
            secrets.delete(connector["connector_id"])
            raise HTTPException(status_code=409, detail="device_risk_connector_exists")
    except CredentialStoreError as exc:
        raise HTTPException(status_code=503, detail=exc.code) from exc
    except Exception:
        try:
            secrets.delete(connector["connector_id"])
        except CredentialStoreError:
            logger.error(
                "Unable to remove unconfigured device-risk connector secret",
                extra={"merchant_id": merchant_id, "connector_id": connector["connector_id"]},
            )
        raise
    for connector_id in superseded:
        store.update_device_risk_connector_status(
            merchant_id,
            connector_id,
            status="disconnected",
            last_error_code=None,
            audit_action="superseded",
        )
        try:
            secrets.delete(connector_id)
        except CredentialStoreError as exc:
            logger.error(
                "Unable to remove superseded device-risk connector secret",
                extra={
                    "merchant_id": merchant_id,
                    "connector_id": connector_id,
                    "error_code": exc.code,
                },
            )
    return connector


@router.get(
    "/{merchant_id}/device-risk-connectors",
    response_model=list[DeviceRiskConnectorResponse],
)
def list_device_risk_connectors(merchant_id: str) -> list[DeviceRiskConnector]:
    _require_merchant(merchant_id)
    return store.list_device_risk_connectors(merchant_id)


@router.post(
    "/{merchant_id}/device-risk-connectors/{connector_id}/verify",
    response_model=DeviceRiskConnectorResponse,
)
def verify_device_risk_connector(
    merchant_id: str,
    connector_id: str,
) -> DeviceRiskConnector:
    connector = store.get_device_risk_connector(merchant_id, connector_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="Device-risk connector not found.")
    if connector["status"] == "disconnected":
        raise HTTPException(status_code=409, detail="device_risk_connector_disconnected")
    if connector["status"] == "invalid":
        raise HTTPException(status_code=409, detail="device_risk_connector_invalid")
    error_code = (
        "verification_requires_first_real_request"
        if connector["status"] == "verification_pending"
        else None
    )
    updated = store.update_device_risk_connector_status(
        merchant_id,
        connector_id,
        last_error_code=error_code,
        audit_action="verification_deferred" if error_code else "verification_confirmed",
    )
    assert updated is not None
    return updated


@router.delete(
    "/{merchant_id}/device-risk-connectors/{connector_id}",
    response_model=DeviceRiskConnectorResponse,
)
def disconnect_device_risk_connector(
    merchant_id: str,
    connector_id: str,
) -> DeviceRiskConnector:
    if store.get_device_risk_connector(merchant_id, connector_id) is None:
        raise HTTPException(status_code=404, detail="Device-risk connector not found.")
    updated = store.disconnect_device_risk_connector(merchant_id, connector_id)
    assert updated is not None
    try:
        _secret_store().delete(connector_id)
    except CredentialStoreError as exc:
        logger.error(
            "Unable to delete disconnected device-risk connector secret",
            extra={
                "merchant_id": merchant_id,
                "connector_id": connector_id,
                "error_code": exc.code,
            },
        )
        raise HTTPException(status_code=503, detail=exc.code) from exc
    return updated
