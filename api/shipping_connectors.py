"""Authenticated APIs for merchant-owned read-only shipping credentials."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Literal, TypedDict
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from api.auth import require_api_key
from api.schemas import (
    DelhiveryConnectorCreate,
    ShiprocketConnectorCreate,
    ShippingConnectorResponse,
    ShippingConnectorVerify,
)
from api.store import store
from core.state import ShippingConnector
from integrations.credential_secrets import (
    CredentialSecretStore, CredentialStoreError, credential_secret_store_from_env,
)
from integrations.delhivery import DelhiveryClient, DelhiveryRequestError
from integrations.shiprocket import ShiprocketClient, ShiprocketRequestError


router = APIRouter(prefix="/merchants", tags=["shipping-connectors"], dependencies=[Depends(require_api_key)])
logger = logging.getLogger(__name__)
_VERIFICATION_TIMEOUT_SECONDS = 10.0


class VerificationResult(TypedDict):
    verified: bool
    error_code: str | None


def _tracking_id_from_shiprocket(raw: dict, fallback: str) -> str | None:
    data = raw.get("tracking_data") if isinstance(raw.get("tracking_data"), dict) else raw
    shipments = data.get("shipment_track") if isinstance(data, dict) else None
    shipment = shipments[0] if isinstance(shipments, list) and shipments else data
    return str((shipment or {}).get("awb_code") or (shipment or {}).get("awb") or raw.get("tracking_id") or fallback or "") or None


def _tracking_id_from_delhivery(raw: dict, fallback: str) -> str | None:
    entries = raw.get("ShipmentData")
    wrapper = entries[0] if isinstance(entries, list) and entries else raw
    shipment = wrapper.get("Shipment") if isinstance(wrapper, dict) else None
    shipment = shipment or wrapper
    return str((shipment or {}).get("AWB") or (shipment or {}).get("Waybill") or raw.get("tracking_id") or fallback or "") or None


def _verification_error(exc: ShiprocketRequestError | DelhiveryRequestError) -> VerificationResult:
    if exc.status_code in {401, 403}:
        return {"verified": False, "error_code": "provider_authentication_failed"}
    if exc.status_code == 404:
        return {"verified": False, "error_code": "provider_tracking_not_found"}
    return {"verified": False, "error_code": "provider_verification_failed"}


def verify_shiprocket_credentials(email: str, password: str, tracking_id: str) -> VerificationResult:
    try:
        raw = ShiprocketClient(email=email, password=password, timeout=_VERIFICATION_TIMEOUT_SECONDS).get_tracking(tracking_id)
    except ShiprocketRequestError as exc:
        return _verification_error(exc)
    except httpx.HTTPError:
        return {"verified": False, "error_code": "provider_unavailable"}
    return {"verified": _tracking_id_from_shiprocket(raw, "") == tracking_id,
            "error_code": None if _tracking_id_from_shiprocket(raw, "") == tracking_id else "provider_tracking_not_found"}


def verify_delhivery_credentials(api_token: str, tracking_id: str) -> VerificationResult:
    try:
        raw = DelhiveryClient(api_token=api_token, timeout=_VERIFICATION_TIMEOUT_SECONDS).get_tracking(tracking_id)
    except DelhiveryRequestError as exc:
        return _verification_error(exc)
    except httpx.HTTPError:
        return {"verified": False, "error_code": "provider_unavailable"}
    return {"verified": _tracking_id_from_delhivery(raw, "") == tracking_id,
            "error_code": None if _tracking_id_from_delhivery(raw, "") == tracking_id else "provider_tracking_not_found"}


def _metadata(merchant_id: str, provider: Literal["shiprocket", "delhivery"], hint: str) -> ShippingConnector:
    now = datetime.now(timezone.utc)
    return {"connector_id": f"shipcon_{uuid4().hex}", "merchant_id": merchant_id,
            "provider": provider, "status": "pending", "credential_hint": hint,
            "verified_at": None, "created_at": now, "updated_at": now, "last_error_code": None}


def _require_merchant(merchant_id: str) -> None:
    if store.get_merchant(merchant_id) is None:
        raise HTTPException(status_code=404, detail="Merchant not found.")


def _secret_store() -> CredentialSecretStore:
    try:
        return credential_secret_store_from_env()
    except CredentialStoreError as exc:
        raise HTTPException(status_code=503, detail=exc.code) from exc


def _complete_connection(connector: ShippingConnector, credentials: dict[str, str], verification: VerificationResult) -> ShippingConnector:
    store.create_shipping_connector(connector, audit_action="created")
    if not verification["verified"]:
        return store.update_shipping_connector_status(
            connector["merchant_id"], connector["connector_id"], status="invalid",
            last_error_code=verification["error_code"], audit_action="verification_failed",
        ) or connector
    secrets = _secret_store()
    connector["status"] = "verified"
    connector["verified_at"] = datetime.now(timezone.utc)
    connector["updated_at"] = connector["verified_at"]
    try:
        secrets.put(connector["connector_id"], credentials)
        previous_id = store.activate_shipping_connector(connector, audit_action="verified")
    except CredentialStoreError as exc:
        store.update_shipping_connector_status(connector["merchant_id"], connector["connector_id"],
                                                status="invalid", last_error_code=exc.code,
                                                audit_action="storage_failed")
        raise HTTPException(status_code=503, detail=exc.code) from exc
    except Exception:
        try:
            secrets.delete(connector["connector_id"])
        except CredentialStoreError:
            logger.error("Unable to remove inactive shipping connector secret", extra={"connector_id": connector["connector_id"]})
        raise
    if previous_id and previous_id != connector["connector_id"]:
        try:
            secrets.delete(previous_id)
        except CredentialStoreError as exc:
            logger.error("Unable to remove rotated shipping connector secret", extra={"connector_id": previous_id, "error_code": exc.code})
    return connector


@router.post("/{merchant_id}/shipping-connectors/shiprocket", response_model=ShippingConnectorResponse, status_code=status.HTTP_201_CREATED)
def connect_shiprocket(merchant_id: str, payload: ShiprocketConnectorCreate) -> ShippingConnector:
    _require_merchant(merchant_id)
    return _complete_connection(
        _metadata(merchant_id, "shiprocket", "Shiprocket account configured"),
        {"email": payload.email, "password": payload.password},
        verify_shiprocket_credentials(payload.email, payload.password, payload.verification_tracking_id),
    )


@router.post("/{merchant_id}/shipping-connectors/delhivery", response_model=ShippingConnectorResponse, status_code=status.HTTP_201_CREATED)
def connect_delhivery(merchant_id: str, payload: DelhiveryConnectorCreate) -> ShippingConnector:
    _require_merchant(merchant_id)
    return _complete_connection(
        _metadata(merchant_id, "delhivery", f"ending in {payload.api_token[-4:]}"),
        {"api_token": payload.api_token},
        verify_delhivery_credentials(payload.api_token, payload.verification_tracking_id),
    )


@router.get("/{merchant_id}/shipping-connectors", response_model=list[ShippingConnectorResponse])
def list_shipping_connectors(merchant_id: str) -> list[ShippingConnector]:
    _require_merchant(merchant_id)
    return store.list_shipping_connectors(merchant_id)


@router.post("/{merchant_id}/shipping-connectors/{connector_id}/verify", response_model=ShippingConnectorResponse)
def verify_shipping_connector(merchant_id: str, connector_id: str, payload: ShippingConnectorVerify) -> ShippingConnector:
    connector = store.get_shipping_connector(merchant_id, connector_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="Shipping connector not found.")
    if connector["status"] == "disconnected":
        raise HTTPException(status_code=409, detail="shipping_connector_disconnected")
    try:
        credentials = _secret_store().get(connector_id)
    except CredentialStoreError as exc:
        raise HTTPException(status_code=409, detail=exc.code) from exc
    verification = (verify_shiprocket_credentials(credentials.get("email", ""), credentials.get("password", ""), payload.verification_tracking_id)
                    if connector["provider"] == "shiprocket"
                    else verify_delhivery_credentials(credentials.get("api_token", ""), payload.verification_tracking_id))
    if verification["verified"]:
        connector["status"] = "verified"
        connector["last_error_code"] = None
        connector["verified_at"] = datetime.now(timezone.utc)
        connector["updated_at"] = connector["verified_at"]
        store.activate_shipping_connector(connector, audit_action="verified")
        return store.get_shipping_connector(merchant_id, connector_id) or connector
    return store.update_shipping_connector_status(
        merchant_id, connector_id, status="invalid" if verification["error_code"] == "provider_authentication_failed" else None,
        last_error_code=verification["error_code"], audit_action="verification_failed",
    ) or connector


@router.delete("/{merchant_id}/shipping-connectors/{connector_id}", response_model=ShippingConnectorResponse)
def disconnect_shipping_connector(merchant_id: str, connector_id: str) -> ShippingConnector:
    if store.get_shipping_connector(merchant_id, connector_id) is None:
        raise HTTPException(status_code=404, detail="Shipping connector not found.")
    updated = store.disconnect_shipping_connector(merchant_id, connector_id)
    assert updated is not None
    try:
        _secret_store().delete(connector_id)
    except CredentialStoreError as exc:
        logger.error("Unable to delete disconnected shipping connector secret", extra={"connector_id": connector_id, "error_code": exc.code})
        raise HTTPException(status_code=503, detail=exc.code) from exc
    return updated
