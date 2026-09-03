from typing import TypedDict
from urllib.parse import urlsplit

import httpx

from integrations.platform_detect import StoreURLValidationError, normalize_store_url


class CredentialVerification(TypedDict):
    verified: bool
    reason: str


def verify_shopify_credential(
    store_url: str,
    token: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = 10.0,
) -> CredentialVerification:
    return _verify(
        store_url,
        "/admin/api/2024-01/shop.json",
        client=client,
        timeout=timeout,
        headers={"X-Shopify-Access-Token": token},
        provider="shopify",
    )


def verify_woocommerce_credential(
    store_url: str,
    key: str,
    secret: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = 10.0,
) -> CredentialVerification:
    return _verify(
        store_url,
        "/wp-json/wc/v3/system_status",
        client=client,
        timeout=timeout,
        auth=(key, secret),
        provider="woocommerce",
    )


def _verify(
    store_url: str,
    path: str,
    *,
    client: httpx.Client | None,
    timeout: float,
    provider: str,
    headers: dict[str, str] | None = None,
    auth: tuple[str, str] | None = None,
) -> CredentialVerification:
    try:
        base_url = normalize_store_url(store_url, resolve_host=client is None).rstrip("/")
        if urlsplit(base_url).scheme != "https":
            return {"verified": False, "reason": f"{provider}_https_required"}
        if client is None:
            with httpx.Client(timeout=timeout) as owned_client:
                response = owned_client.get(f"{base_url}{path}", headers=headers, auth=auth)
        else:
            response = client.get(f"{base_url}{path}", headers=headers, auth=auth)
    except (httpx.HTTPError, OSError, StoreURLValidationError):
        return {"verified": False, "reason": f"{provider}_unreachable"}

    if response.status_code in {401, 403}:
        return {"verified": False, "reason": f"{provider}_credential_rejected"}
    if response.status_code == 404:
        return {"verified": False, "reason": f"{provider}_endpoint_not_found"}
    if response.status_code != 200:
        return {"verified": False, "reason": f"{provider}_verification_failed_{response.status_code}"}
    try:
        payload = response.json()
    except ValueError:
        return {"verified": False, "reason": f"{provider}_invalid_response"}
    if not isinstance(payload, dict):
        return {"verified": False, "reason": f"{provider}_invalid_response"}
    if provider == "shopify" and not isinstance(payload.get("shop"), dict):
        return {"verified": False, "reason": "shopify_invalid_shop_payload"}
    return {"verified": True, "reason": f"{provider}_credential_verified"}
