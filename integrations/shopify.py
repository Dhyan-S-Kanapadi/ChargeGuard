from datetime import datetime, timezone
import logging
import time
from typing import Any, Callable, TypedDict
from urllib.parse import urlsplit

import httpx

from core.state import MerchantProfile, OrderRecord
from integrations.platform_detect import normalize_store_url


logger = logging.getLogger(__name__)


class ShopifySyncResult(TypedDict):
    created: int
    updated: int
    failed_pages: int


def sync_shopify_history(
    merchant: MerchantProfile,
    upsert: Callable[[OrderRecord], bool],
    *,
    client: httpx.Client | None = None,
    timeout: float = 10.0,
    sleep: Callable[[float], None] = time.sleep,
) -> ShopifySyncResult:
    token = merchant.get("shopify_admin_api_token")
    store_url = merchant.get("store_url")
    if not token or not store_url:
        raise ValueError("Verified Shopify store URL and token are required.")

    base_url = normalize_store_url(store_url, resolve_host=client is None).rstrip("/")
    if urlsplit(base_url).scheme != "https":
        raise ValueError("Shopify sync requires an HTTPS store URL.")
    url: str | None = f"{base_url}/admin/api/2024-01/orders.json?status=any&limit=250"
    result: ShopifySyncResult = {"created": 0, "updated": 0, "failed_pages": 0}

    if client is None:
        with httpx.Client(timeout=timeout) as owned_client:
            _sync_pages(
                merchant,
                token,
                url,
                upsert,
                owned_client,
                sleep,
                result,
                allowed_host=urlsplit(base_url).hostname or "",
            )
    else:
        _sync_pages(
            merchant,
            token,
            url,
            upsert,
            client,
            sleep,
            result,
            allowed_host=urlsplit(base_url).hostname or "",
        )
    return result


def _sync_pages(
    merchant: MerchantProfile,
    token: str,
    url: str | None,
    upsert: Callable[[OrderRecord], bool],
    client: httpx.Client,
    sleep: Callable[[float], None],
    result: ShopifySyncResult,
    *,
    allowed_host: str,
) -> None:
    seen_urls: set[str] = set()
    while url and url not in seen_urls:
        seen_urls.add(url)
        response: httpx.Response | None = None
        for attempt in range(4):
            try:
                response = client.get(url, headers={"X-Shopify-Access-Token": token})
            except httpx.HTTPError:
                logger.warning("Shopify order page request failed", extra={"merchant_id": merchant["merchant_id"]})
                break
            if response.status_code != 429:
                break
            if attempt == 3:
                break
            retry_after = _retry_after(response)
            logger.info("Shopify rate limit reached; retrying", extra={"merchant_id": merchant["merchant_id"]})
            sleep(retry_after)

        next_url = _next_link(response, allowed_host=allowed_host) if response is not None else None
        if response is None or response.status_code != 200:
            result["failed_pages"] += 1
            logger.warning(
                "Shopify order page was skipped",
                extra={
                    "merchant_id": merchant["merchant_id"],
                    "status_code": response.status_code if response is not None else None,
                },
            )
            url = next_url
            continue

        try:
            payload = response.json()
            orders = payload["orders"]
            if not isinstance(orders, list):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            result["failed_pages"] += 1
            logger.warning("Shopify order page returned malformed data", extra={"merchant_id": merchant["merchant_id"]})
            url = next_url
            continue

        for item in orders:
            order = _order_record(merchant["merchant_id"], item)
            if order is None:
                continue
            try:
                created = upsert(order)
            except ValueError:
                logger.warning(
                    "Shopify order was skipped because its identifiers conflicted",
                    extra={"merchant_id": merchant["merchant_id"]},
                )
                continue
            result["created" if created else "updated"] += 1
        _respect_call_limit(response, sleep)
        url = next_url


def _order_record(merchant_id: str, item: Any) -> OrderRecord | None:
    if not isinstance(item, dict) or item.get("id") is None:
        return None
    try:
        order_date = datetime.fromisoformat(str(item["created_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None
    order_date = order_date.replace(tzinfo=order_date.tzinfo or timezone.utc).astimezone(timezone.utc)
    client_details = item.get("client_details") if isinstance(item.get("client_details"), dict) else {}
    shipping = item.get("shipping_address") if isinstance(item.get("shipping_address"), dict) else ""
    record: OrderRecord = {
        "order_id": str(item["id"]),
        "merchant_id": merchant_id,
        "customer_email": str(item.get("email") or item.get("contact_email") or "").strip().casefold(),
        "customer_ip": str(client_details.get("browser_ip") or ""),
        "user_agent": str(client_details.get("user_agent") or ""),
        "shipping_address": shipping,
        "order_date": order_date,
        "is_disputed": False,
        "is_fraud_flagged": False,
    }
    commerce_number = item.get("name") or item.get("order_number")
    if commerce_number is not None and str(commerce_number).strip():
        record["commerce_order_number"] = str(commerce_number).strip()

    fulfillment_id, tracking_id = _fulfillment_identifiers(item)
    if fulfillment_id:
        record["fulfillment_id"] = fulfillment_id
    if tracking_id:
        record["tracking_id"] = tracking_id

    provider_payment_id = _explicit_provider_identifier(
        item,
        "provider_payment_id",
        "razorpay_payment_id",
    )
    provider_order_id = _explicit_provider_identifier(
        item,
        "provider_order_id",
        "razorpay_order_id",
    )
    if provider_payment_id or provider_order_id:
        record["payment_provider"] = "razorpay"
    if provider_payment_id:
        record["provider_payment_id"] = provider_payment_id
    if provider_order_id:
        record["provider_order_id"] = provider_order_id
    return record


def _explicit_provider_identifier(item: dict[str, Any], *names: str) -> str | None:
    for name in names:
        value = item.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    metadata = item.get("metadata")
    if isinstance(metadata, dict):
        for name in names:
            value = metadata.get(name)
            if value is not None and str(value).strip():
                return str(value).strip()
    attributes = item.get("note_attributes")
    if not isinstance(attributes, list):
        return None
    allowed = {name.casefold() for name in names}
    for attribute in attributes:
        if not isinstance(attribute, dict):
            continue
        if str(attribute.get("name") or "").strip().casefold() not in allowed:
            continue
        value = attribute.get("value")
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _fulfillment_identifiers(item: dict[str, Any]) -> tuple[str | None, str | None]:
    fulfillments = item.get("fulfillments")
    if not isinstance(fulfillments, list):
        return None, None
    for fulfillment in fulfillments:
        if not isinstance(fulfillment, dict):
            continue
        fulfillment_id = fulfillment.get("id")
        tracking_id = fulfillment.get("tracking_number")
        if not tracking_id:
            numbers = fulfillment.get("tracking_numbers")
            if isinstance(numbers, list) and len(numbers) == 1:
                tracking_id = numbers[0]
        return (
            str(fulfillment_id).strip() if fulfillment_id is not None else None,
            str(tracking_id).strip() if tracking_id is not None else None,
        )
    return None, None


def _next_link(response: httpx.Response | None, *, allowed_host: str) -> str | None:
    if response is None:
        return None
    link = response.links.get("next")
    if not link or not link.get("url"):
        return None
    url = str(link["url"])
    parsed = urlsplit(url)
    if parsed.scheme != "https" or (parsed.hostname or "").casefold() != allowed_host.casefold():
        logger.warning("Ignoring unsafe Shopify pagination URL")
        return None
    return url


def _retry_after(response: httpx.Response) -> float:
    try:
        return min(max(float(response.headers.get("Retry-After", "1")), 0.0), 60.0)
    except ValueError:
        return 1.0


def _respect_call_limit(response: httpx.Response, sleep: Callable[[float], None]) -> None:
    value = response.headers.get("X-Shopify-Shop-Api-Call-Limit")
    if not value:
        return
    try:
        used, limit = (int(part) for part in value.split("/", 1))
    except (TypeError, ValueError):
        return
    if limit - used <= 2:
        sleep(0.5)
