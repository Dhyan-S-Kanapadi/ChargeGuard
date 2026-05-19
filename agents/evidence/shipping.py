import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from core.state import ChargebackState, ShippingEvidence


logger = logging.getLogger(__name__)


def _empty_shipping_evidence(
    state: ChargebackState,
    *,
    error: str | None = None,
) -> ShippingEvidence:
    return {
        "tracking_id": state.get("tracking_id", ""),
        "courier": "",
        "status": "UNKNOWN",
        "delivered_at": None,
        "delivery_latitude": None,
        "delivery_longitude": None,
        "signature_obtained": False,
        "delivery_photo_url": None,
        "raw": {
            "source": "shipping_agent_empty",
            "error": error,
        },
    }


def _stub_tracking_response(state: ChargebackState) -> dict[str, Any]:
    tracking_id = state.get("tracking_id") or f"trk_{state.get('order_id', state['chargeback_id'])}"
    delivered_at = state["filing_deadline"] - timedelta(days=12)

    return {
        "tracking_id": tracking_id,
        "courier": "Shiprocket",
        "status": "DELIVERED",
        "delivered_at": delivered_at.isoformat(),
        "delivery_location": {
            "latitude": 12.9716,
            "longitude": 77.5946,
        },
        "proof_of_delivery": {
            "signature_obtained": True,
            "photo_url": f"https://example.test/pod/{tracking_id}.jpg",
        },
        "events": [
            {
                "status": "PICKED_UP",
                "timestamp": (delivered_at - timedelta(days=3)).isoformat(),
            },
            {
                "status": "OUT_FOR_DELIVERY",
                "timestamp": (delivered_at - timedelta(hours=6)).isoformat(),
            },
            {
                "status": "DELIVERED",
                "timestamp": delivered_at.isoformat(),
            },
        ],
    }


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _build_shipping_evidence(
    tracking: dict[str, Any],
) -> ShippingEvidence:
    location = tracking.get("delivery_location") or {}
    proof = tracking.get("proof_of_delivery") or {}

    return {
        "tracking_id": str(tracking.get("tracking_id") or ""),
        "courier": str(tracking.get("courier") or ""),
        "status": str(tracking.get("status") or "UNKNOWN"),
        "delivered_at": _parse_datetime(tracking.get("delivered_at")),
        "delivery_latitude": location.get("latitude"),
        "delivery_longitude": location.get("longitude"),
        "signature_obtained": bool(proof.get("signature_obtained")),
        "delivery_photo_url": proof.get("photo_url"),
        "raw": {
            "source": "shipping_agent_stub",
            "tracking": tracking,
        },
    }


def shipping_agent(state: ChargebackState) -> ChargebackState:
    logger.info("Running shipping agent")

    try:
        tracking = _stub_tracking_response(state)
        state["shipping"] = _build_shipping_evidence(tracking)
    except Exception as exc:
        logger.exception("Shipping evidence collection failed")
        state["shipping"] = _empty_shipping_evidence(state, error=str(exc))

    return state
