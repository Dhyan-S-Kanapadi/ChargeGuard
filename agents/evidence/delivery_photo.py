import logging
import os
from datetime import datetime, timezone
from typing import Any

from core.state import ChargebackState, DeliveryPhotoEvidence
from integrations.claude_vision import ClaudeVisionClient
from integrations.food_platform import FoodPlatformClient


logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _shipping_context(state: ChargebackState) -> dict:
    shipping = state.get("shipping") or {}
    return {
        "photo_url": shipping.get("delivery_photo_url") or "",
        "delivered_at": shipping.get("delivered_at"),
        "status": shipping.get("status", "UNKNOWN"),
    }


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Unable to parse delivery photo timestamp %r", value)
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _empty_delivery_photo_evidence(
    state: ChargebackState,
    *,
    error: str | None = None,
) -> DeliveryPhotoEvidence:
    context = _shipping_context(state)
    return {
        "photo_url": context["photo_url"],
        "ai_verified": False,
        "address_visible": False,
        "timestamp_on_photo": None,
        "raw": {
            "source": "delivery_photo_agent_empty",
            "error": error,
            "shipping_status": context["status"],
        },
    }


def _stub_photo_verification_response(state: ChargebackState) -> dict[str, Any]:
    context = _shipping_context(state)
    return {
        "photo_url": context["photo_url"],
        "ai_verified": bool(context["photo_url"]),
        "address_visible": bool(context["photo_url"]),
        "timestamp_on_photo": context["delivered_at"],
        "shipping_status": context["status"],
    }


def _platform_photo_response(state: ChargebackState) -> dict[str, Any]:
    order_id = state.get("order_id")
    if not order_id:
        raise ValueError("Delivery photo collection requires order_id")
    return FoodPlatformClient.from_env().get_delivery_photo(order_id)


def _collect_delivery_photo_data(state: ChargebackState) -> tuple[dict[str, Any], str]:
    if _env_flag("CHARGEGUARD_USE_STUBS"):
        return _stub_photo_verification_response(state), "delivery_photo_agent_stub"

    context = _shipping_context(state)
    platform_response: dict[str, Any] = {}
    photo_url = context["photo_url"]

    if not photo_url:
        platform_response = _platform_photo_response(state)
        photo_url = str(
            platform_response.get("photo_url")
            or platform_response.get("image_url")
            or platform_response.get("pod_url")
            or ""
        )

    if not photo_url:
        raise ValueError("Delivery photo collection requires a photo URL")

    vision = ClaudeVisionClient.from_env().verify_delivery_photo(photo_url)
    return {
        "photo_url": photo_url,
        "ai_verified": vision.get("delivered"),
        "address_visible": vision.get("address_visible"),
        "confidence": vision.get("confidence"),
        "timestamp_on_photo": platform_response.get("timestamp_on_photo")
        or platform_response.get("captured_at")
        or context["delivered_at"],
        "shipping_status": context["status"],
        "platform_response": platform_response,
        "vision_response": vision,
    }, "claude_vision"


def _build_delivery_photo_evidence(
    response: dict[str, Any],
    *,
    source: str = "delivery_photo_agent_stub",
) -> DeliveryPhotoEvidence:
    evidence: DeliveryPhotoEvidence = {
        "photo_url": str(response.get("photo_url") or response.get("image_url") or ""),
        "ai_verified": bool(
            response.get("ai_verified")
            or response.get("delivery_match")
            or response.get("delivered")
        ),
        "address_visible": bool(response.get("address_visible") or response.get("doorstep_visible")),
        "timestamp_on_photo": _parse_datetime(
            response.get("timestamp_on_photo") or response.get("captured_at")
        ),
        "raw": {
            "source": source,
            "response": response,
        },
    }
    return evidence


def delivery_photo_agent(state: ChargebackState) -> ChargebackState:
    """Verify proof-of-delivery photo evidence for food/quick-commerce disputes."""
    logger.info("Running delivery photo evidence agent for %s", state["chargeback_id"])

    try:
        response, source = _collect_delivery_photo_data(state)
        state["delivery_photo"] = _build_delivery_photo_evidence(response, source=source)
    except Exception as exc:
        logger.exception("Delivery photo evidence collection failed")
        state["delivery_photo"] = _empty_delivery_photo_evidence(state, error=str(exc))

    return state
