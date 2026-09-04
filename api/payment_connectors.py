"""Authenticated operator APIs for merchant-scoped payment credentials."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Literal, TypedDict
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from api.auth import require_api_key
from api.schemas import (
    PaymentConnectorResponse,
    RazorpayConnectorCreate,
    StripeConnectorCreate,
)
from api.store import store
from core.state import PaymentConnector
from integrations.credential_secrets import (
    CredentialSecretStore,
    CredentialStoreError,
    credential_secret_store_from_env,
)
from integrations.razorpay import RazorpayClient, RazorpayRequestError
from integrations.stripe import StripeClient, StripeRequestError


router = APIRouter(
    prefix="/merchants",
    tags=["payment-connectors"],
    dependencies=[Depends(require_api_key)],
)
logger = logging.getLogger(__name__)
_VERIFICATION_TIMEOUT_SECONDS = 10.0


class VerificationResult(TypedDict):
    verified: bool
    error_code: str | None
    provider_account_id: str | None


def verify_razorpay_credentials(key_id: str, key_secret: str) -> VerificationResult:
    try:
        RazorpayClient(
            key_id=key_id,
            key_secret=key_secret,
            timeout=_VERIFICATION_TIMEOUT_SECONDS,
        ).verify_credentials()
    except RazorpayRequestError as exc:
        code = (
            "provider_authentication_failed"
            if exc.status_code in {401, 403}
            else "provider_verification_failed"
        )
        return {"verified": False, "error_code": code, "provider_account_id": None}
    except httpx.HTTPError:
        return {"verified": False, "error_code": "provider_unavailable", "provider_account_id": None}
    return {"verified": True, "error_code": None, "provider_account_id": None}


def verify_stripe_credentials(api_key: str) -> VerificationResult:
    try:
        account_id = StripeClient(
            api_key=api_key,
            timeout=_VERIFICATION_TIMEOUT_SECONDS,
        ).verify_credentials()
    except StripeRequestError as exc:
        code = (
            "provider_authentication_failed"
            if exc.status_code in {401, 403}
            else "provider_verification_failed"
        )
        return {"verified": False, "error_code": code, "provider_account_id": None}
    except httpx.HTTPError:
        return {"verified": False, "error_code": "provider_unavailable", "provider_account_id": None}
    return {"verified": True, "error_code": None, "provider_account_id": account_id}


def _metadata(
    *,
    merchant_id: str,
    provider: Literal["razorpay", "stripe"],
    provider_account_id: str | None,
    credential_hint: str,
) -> PaymentConnector:
    now = datetime.now(timezone.utc)
    return {
        "connector_id": f"paycon_{uuid4().hex}",
        "merchant_id": merchant_id,
        "provider": provider,
        "provider_account_id": provider_account_id,
        "status": "pending",
        "credential_hint": credential_hint,
        "verified_at": None,
        "created_at": now,
        "updated_at": now,
        "last_error_code": None,
    }


def _hint(value: str) -> str:
    return f"ending in {value[-4:]}"


def _require_merchant(merchant_id: str) -> None:
    if store.get_merchant(merchant_id) is None:
        raise HTTPException(status_code=404, detail="Merchant not found.")


def _safe_secret_store() -> CredentialSecretStore:
    try:
        return credential_secret_store_from_env()
    except CredentialStoreError as exc:
        raise HTTPException(status_code=503, detail=exc.code) from exc


def _remove_secret_after_failed_activation(
    secret_store: CredentialSecretStore,
    connector: PaymentConnector,
) -> None:
    try:
        secret_store.delete(connector["connector_id"])
    except CredentialStoreError as exc:
        logger.error(
            "Unable to remove inactive payment connector secret",
            extra={
                "merchant_id": connector["merchant_id"],
                "connector_id": connector["connector_id"],
                "error_code": exc.code,
            },
        )


def _complete_connection(
    connector: PaymentConnector,
    credentials: dict[str, str],
    verification: VerificationResult,
) -> PaymentConnector:
    secret_store = _safe_secret_store() if verification["verified"] else None
    store.create_payment_connector(connector, audit_action="created")
    if not verification["verified"]:
        return store.update_payment_connector_status(
            connector["merchant_id"],
            connector["connector_id"],
            status="invalid",
            last_error_code=verification["error_code"],
            audit_action="verification_failed",
        ) or connector

    connector["provider_account_id"] = (
        verification["provider_account_id"] or connector["provider_account_id"]
    )
    connector["status"] = "verified"
    connector["verified_at"] = datetime.now(timezone.utc)
    connector["updated_at"] = connector["verified_at"]
    merchant = store.get_merchant(connector["merchant_id"])
    existing_id = (merchant or {}).get("payment_connector_ids", {}).get(
        connector["provider"]
    )
    assert secret_store is not None
    try:
        secret_store.put(connector["connector_id"], credentials)
        previous_id = store.activate_payment_connector(
            connector,
            audit_action="rotated" if existing_id else "verified",
        )
    except CredentialStoreError as exc:
        store.update_payment_connector_status(
            connector["merchant_id"],
            connector["connector_id"],
            status="invalid",
            last_error_code=exc.code,
            audit_action="storage_failed",
        )
        raise HTTPException(status_code=503, detail=exc.code) from exc
    except ValueError as exc:
        _remove_secret_after_failed_activation(secret_store, connector)
        store.update_payment_connector_status(
            connector["merchant_id"],
            connector["connector_id"],
            status="invalid",
            last_error_code=str(exc),
            audit_action="activation_failed",
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        _remove_secret_after_failed_activation(secret_store, connector)
        raise

    if previous_id and previous_id != connector["connector_id"]:
        try:
            secret_store.delete(previous_id)
        except CredentialStoreError as exc:
            logger.error(
                "Unable to remove rotated payment connector secret",
                extra={
                    "merchant_id": connector["merchant_id"],
                    "connector_id": previous_id,
                    "error_code": exc.code,
                },
            )
    return connector


@router.post(
    "/{merchant_id}/payment-connectors/razorpay",
    response_model=PaymentConnectorResponse,
    status_code=status.HTTP_201_CREATED,
)
def connect_razorpay(
    merchant_id: str,
    payload: RazorpayConnectorCreate,
) -> PaymentConnector:
    _require_merchant(merchant_id)
    connector = _metadata(
        merchant_id=merchant_id,
        provider="razorpay",
        provider_account_id=payload.razorpay_account_id,
        credential_hint=_hint(payload.key_id),
    )
    verification = verify_razorpay_credentials(payload.key_id, payload.key_secret)
    return _complete_connection(
        connector,
        {"key_id": payload.key_id, "key_secret": payload.key_secret},
        verification,
    )


@router.post(
    "/{merchant_id}/payment-connectors/stripe",
    response_model=PaymentConnectorResponse,
    status_code=status.HTTP_201_CREATED,
)
def connect_stripe(
    merchant_id: str,
    payload: StripeConnectorCreate,
) -> PaymentConnector:
    _require_merchant(merchant_id)
    connector = _metadata(
        merchant_id=merchant_id,
        provider="stripe",
        provider_account_id=None,
        credential_hint=_hint(payload.api_key),
    )
    verification = verify_stripe_credentials(payload.api_key)
    return _complete_connection(connector, {"api_key": payload.api_key}, verification)


@router.get(
    "/{merchant_id}/payment-connectors",
    response_model=list[PaymentConnectorResponse],
)
def list_payment_connectors(merchant_id: str) -> list[PaymentConnector]:
    _require_merchant(merchant_id)
    return store.list_payment_connectors(merchant_id)


@router.post(
    "/{merchant_id}/payment-connectors/{connector_id}/verify",
    response_model=PaymentConnectorResponse,
)
def verify_payment_connector(merchant_id: str, connector_id: str) -> PaymentConnector:
    connector = store.get_payment_connector(merchant_id, connector_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="Payment connector not found.")
    if connector["status"] == "disconnected":
        raise HTTPException(status_code=409, detail="payment_connector_disconnected")
    secret_store = _safe_secret_store()
    try:
        credentials = secret_store.get(connector_id)
    except CredentialStoreError as exc:
        raise HTTPException(status_code=409, detail=exc.code) from exc

    if connector["provider"] == "razorpay":
        verification = verify_razorpay_credentials(
            credentials.get("key_id", ""),
            credentials.get("key_secret", ""),
        )
    else:
        verification = verify_stripe_credentials(credentials.get("api_key", ""))
    if verification["verified"]:
        connector["status"] = "verified"
        connector["last_error_code"] = None
        connector["verified_at"] = datetime.now(timezone.utc)
        connector["updated_at"] = connector["verified_at"]
        connector["provider_account_id"] = (
            verification["provider_account_id"] or connector["provider_account_id"]
        )
        store.activate_payment_connector(connector, audit_action="verified")
        updated = store.get_payment_connector(merchant_id, connector_id)
    elif (
        connector["status"] == "verified"
        and verification["error_code"] != "provider_authentication_failed"
    ):
        updated = store.update_payment_connector_status(
            merchant_id,
            connector_id,
            last_error_code=verification["error_code"],
            verified_at=connector["verified_at"],
            audit_action="verification_unavailable",
        )
    else:
        updated = store.update_payment_connector_status(
            merchant_id,
            connector_id,
            status="invalid",
            last_error_code=verification["error_code"],
            audit_action="verification_failed",
        )
    if updated is None:
        raise HTTPException(status_code=404, detail="Payment connector not found.")
    return updated


@router.delete(
    "/{merchant_id}/payment-connectors/{connector_id}",
    response_model=PaymentConnectorResponse,
)
def disconnect_payment_connector(merchant_id: str, connector_id: str) -> PaymentConnector:
    connector = store.get_payment_connector(merchant_id, connector_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="Payment connector not found.")
    secret_store = _safe_secret_store()
    updated = store.disconnect_payment_connector(merchant_id, connector_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Payment connector not found.")
    try:
        secret_store.delete(connector_id)
    except CredentialStoreError as exc:
        logger.error(
            "Unable to delete disconnected payment connector secret",
            extra={
                "merchant_id": merchant_id,
                "connector_id": connector_id,
                "error_code": exc.code,
            },
        )
        raise HTTPException(status_code=503, detail=exc.code) from exc
    return updated
