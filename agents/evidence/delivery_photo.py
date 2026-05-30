import logging
from typing import Any

from core.state import ChargebackState, DeliveryPhotoEvidence


logger = logging.getLogger(__name__)


def _shipping_context(state: ChargebackState) -> dict:
    shipping = state.get("shipping") or {}
    return {
        "photo_url": shipping.get("delivery_photo_url") or "",
        "delivered_at": shipping.get("delivered_at"),
        "status": shipping.get("status", "UNKNOWN"),
    }


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


def _build_delivery_photo_evidence(response: dict[str, Any]) -> DeliveryPhotoEvidence:
    evidence: DeliveryPhotoEvidence = {
        "photo_url": str(response.get("photo_url") or response.get("image_url") or ""),
        "ai_verified": bool(response.get("ai_verified") or response.get("delivery_match")),
        "address_visible": bool(response.get("address_visible") or response.get("doorstep_visible")),
        "timestamp_on_photo": response.get("timestamp_on_photo") or response.get("captured_at"),
        "raw": {
            "source": "delivery_photo_agent_stub",
            "response": response,
        },
    }
    return evidence


def delivery_photo_agent(state: ChargebackState) -> ChargebackState:
    """Verify proof-of-delivery photo evidence for food/quick-commerce disputes."""
    logger.info("Running delivery photo evidence agent for %s", state["chargeback_id"])

    try:
        response = _stub_photo_verification_response(state)
        state["delivery_photo"] = _build_delivery_photo_evidence(response)
    except Exception as exc:
        logger.exception("Delivery photo evidence collection failed")
        state["delivery_photo"] = _empty_delivery_photo_evidence(state, error=str(exc))

    return state
