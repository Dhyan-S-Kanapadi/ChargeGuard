from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from analytics.merchant_stats import merchant_dispute_ratio
from api.auth import require_api_key
from api.schemas import (
    MerchantCreate,
    MerchantResponse,
    MerchantUpdate,
    PlatformSuggestionRequest,
    PlatformSuggestionResponse,
    ShopifySyncResponse,
)
from api.store import store
from core.state import MerchantProfile
from integrations.platform_detect import suggest_storefront_platform
from integrations.shopify import sync_shopify_history
from integrations.storefront_credentials import (
    CredentialVerification,
    verify_shopify_credential,
    verify_woocommerce_credential,
)


router = APIRouter(
    prefix="/merchants",
    tags=["merchants"],
    dependencies=[Depends(require_api_key)],
)


def _response(profile: MerchantProfile) -> MerchantResponse:
    disputes = store.list_disputes()
    configured_networks = profile.get("transaction_volume_30d_by_network", {})
    return MerchantResponse(
        merchant_id=profile["merchant_id"],
        name=profile["name"],
        vertical=profile["vertical"],
        payment_provider=profile.get("payment_provider"),
        razorpay_account_id=profile.get("razorpay_account_id"),
        shipping_provider=profile.get("shipping_provider"),
        support_connector_ref=profile.get("support_connector_ref"),
        freshdesk_domain=profile.get("freshdesk_domain", ""),
        gmail_user_id=profile.get("gmail_user_id"),
        average_order_value=profile["average_order_value"],
        chargeback_history_count=profile["chargeback_history_count"],
        transaction_volume_30d_by_network=configured_networks,
        merchant_dispute_ratio={
            network: merchant_dispute_ratio(profile, disputes, network)
            for network in configured_networks
        },
        store_url=profile.get("store_url"),
        storefront_platform=profile.get("storefront_platform", "unknown"),
        platform_credential_verified=profile.get("platform_credential_verified", False),
        platform_credential_verified_at=profile.get("platform_credential_verified_at"),
        platform_credential_verification_reason=profile.get(
            "platform_credential_verification_reason"
        ),
    )


def _verify_platform_credentials(profile: dict[str, Any]) -> CredentialVerification | None:
    store_url = profile.get("store_url")
    shopify_token = profile.get("shopify_admin_api_token")
    woo_key = profile.get("woocommerce_api_key")
    woo_secret = profile.get("woocommerce_api_secret")
    if shopify_token:
        return verify_shopify_credential(str(store_url or ""), str(shopify_token))
    if woo_key and woo_secret:
        return verify_woocommerce_credential(
            str(store_url or ""), str(woo_key), str(woo_secret)
        )
    return None


def _apply_verification(profile: dict[str, Any], result: CredentialVerification | None) -> None:
    verified = bool(result and result["verified"])
    profile["platform_credential_verified"] = verified
    profile["platform_credential_verified_at"] = (
        datetime.now(timezone.utc) if verified else None
    )
    profile["platform_credential_verification_reason"] = (
        result["reason"] if result else None
    )


@router.post("", response_model=MerchantResponse, status_code=status.HTTP_201_CREATED)
def create_merchant(payload: MerchantCreate) -> MerchantResponse:
    values = payload.model_dump(exclude_none=True)
    result = _verify_platform_credentials(values)
    _apply_verification(values, result)
    if result and not result["verified"]:
        values.pop("shopify_admin_api_token", None)
        values.pop("woocommerce_api_key", None)
        values.pop("woocommerce_api_secret", None)
    profile: MerchantProfile = values  # type: ignore[assignment]
    if not store.create_merchant(profile):
        raise HTTPException(status_code=409, detail="Merchant ID or Razorpay account ID already exists.")
    return _response(profile)


@router.post("/platform-suggestion", response_model=PlatformSuggestionResponse)
def platform_suggestion(payload: PlatformSuggestionRequest) -> PlatformSuggestionResponse:
    return PlatformSuggestionResponse(
        suggestion=suggest_storefront_platform(payload.store_url)
    )


@router.patch("/{merchant_id}", response_model=MerchantResponse)
def update_merchant(merchant_id: str, payload: MerchantUpdate) -> MerchantResponse:
    existing = store.get_merchant(merchant_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Merchant not found.")
    updates = payload.model_dump(exclude_unset=True)
    candidate = {**existing, **updates}
    credential_change = bool(
        {"store_url", "shopify_admin_api_token", "woocommerce_api_key", "woocommerce_api_secret"}
        & updates.keys()
    )
    if credential_change:
        result = _verify_platform_credentials(candidate)
        _apply_verification(updates, result)
        if result and not result["verified"]:
            for key in (
                "shopify_admin_api_token",
                "woocommerce_api_key",
                "woocommerce_api_secret",
            ):
                updates[key] = None
        elif result and candidate.get("shopify_admin_api_token"):
            updates["woocommerce_api_key"] = None
            updates["woocommerce_api_secret"] = None
        elif result:
            updates["shopify_admin_api_token"] = None
    try:
        updated = store.update_merchant(merchant_id, updates)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Merchant not found.")
    return _response(updated)


@router.post("/{merchant_id}/sync-shopify-history", response_model=ShopifySyncResponse)
def sync_shopify_history_endpoint(merchant_id: str) -> ShopifySyncResponse:
    merchant = store.get_merchant(merchant_id)
    if merchant is None:
        raise HTTPException(status_code=404, detail="Merchant not found.")
    if merchant.get("storefront_platform") != "shopify":
        raise HTTPException(status_code=409, detail="Merchant storefront platform is not Shopify.")
    if not merchant.get("platform_credential_verified"):
        raise HTTPException(status_code=409, detail="Shopify credential has not been verified.")
    try:
        result = sync_shopify_history(merchant, store.upsert_order)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ShopifySyncResponse(merchant_id=merchant_id, **result)


@router.get("", response_model=list[MerchantResponse])
def list_merchants() -> list[MerchantResponse]:
    return [_response(profile) for profile in store.list_merchants()]


@router.get("/{merchant_id}", response_model=MerchantResponse)
def get_merchant(merchant_id: str) -> MerchantResponse:
    profile = store.get_merchant(merchant_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Merchant not found.")
    return _response(profile)
