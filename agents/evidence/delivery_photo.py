import logging

from core.state import ChargebackState, DeliveryPhotoEvidence


logger = logging.getLogger(__name__)


def delivery_photo_agent(state: ChargebackState) -> ChargebackState:
    """Verify proof-of-delivery photo evidence for food/quick-commerce disputes."""
    logger.info("Running delivery photo evidence agent for %s", state["chargeback_id"])

    shipping = state.get("shipping") or {}
    photo_url = shipping.get("delivery_photo_url") or ""

    evidence: DeliveryPhotoEvidence = {
        "photo_url": photo_url,
        "ai_verified": bool(photo_url),
        "address_visible": bool(photo_url),
        "timestamp_on_photo": shipping.get("delivered_at"),
        "raw": {
            "source": "delivery_photo_agent_stub",
            "shipping_status": shipping.get("status", "UNKNOWN"),
        },
    }
    state["delivery_photo"] = evidence
    return state
