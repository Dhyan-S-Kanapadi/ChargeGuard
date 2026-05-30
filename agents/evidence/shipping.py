import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from core.state import ChargebackState, ShippingEvidence


logger = logging.getLogger(__name__)


def _bool_from_any(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "signed", "obtained"}
    return bool(value)


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


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Unable to parse shipping timestamp %r", value)
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _extract_location(tracking: dict[str, Any]) -> tuple[float | None, float | None]:
    location = tracking.get("delivery_location") or tracking.get("location") or {}
    gps = tracking.get("gps") or {}

    latitude = (
        location.get("latitude")
        or location.get("lat")
        or gps.get("latitude")
        or gps.get("lat")
        or tracking.get("delivery_latitude")
    )
    longitude = (
        location.get("longitude")
        or location.get("lng")
        or location.get("lon")
        or gps.get("longitude")
        or gps.get("lng")
        or gps.get("lon")
        or tracking.get("delivery_longitude")
    )
    return latitude, longitude


def _extract_proof(tracking: dict[str, Any]) -> dict[str, Any]:
    proof = tracking.get("proof_of_delivery") or tracking.get("pod") or {}
    return {
        "signature_obtained": (
            proof.get("signature_obtained")
            or proof.get("signature")
            or tracking.get("signature_obtained")
            or False
        ),
        "photo_url": proof.get("photo_url") or proof.get("image_url") or tracking.get("delivery_photo_url"),
    }


def _build_shipping_evidence(
    tracking: dict[str, Any],
) -> ShippingEvidence:
    latitude, longitude = _extract_location(tracking)
    proof = _extract_proof(tracking)

    return {
        "tracking_id": str(tracking.get("tracking_id") or tracking.get("awb") or tracking.get("waybill") or ""),
        "courier": str(tracking.get("courier") or tracking.get("carrier") or ""),
        "status": str(tracking.get("status") or tracking.get("current_status") or "UNKNOWN"),
        "delivered_at": _parse_datetime(tracking.get("delivered_at") or tracking.get("delivery_time")),
        "delivery_latitude": latitude,
        "delivery_longitude": longitude,
        "signature_obtained": _bool_from_any(proof.get("signature_obtained")),
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
