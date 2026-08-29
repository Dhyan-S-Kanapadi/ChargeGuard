import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from core.state import ChargebackState, ShippingEvidence
from core.shipping_status import categorize_shipping_status
from integrations.delhivery import DelhiveryClient
from integrations.shiprocket import ShiprocketClient


logger = logging.getLogger(__name__)


class ShippingCollectionError(RuntimeError):
    """Raised after both the primary and fallback carrier fail."""


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


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
        "status_category": "UNKNOWN",
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
    *,
    source: str = "shipping_agent_stub",
) -> ShippingEvidence:
    latitude, longitude = _extract_location(tracking)
    proof = _extract_proof(tracking)

    status = str(tracking.get("status") or tracking.get("current_status") or "UNKNOWN")
    return {
        "tracking_id": str(tracking.get("tracking_id") or tracking.get("awb") or tracking.get("waybill") or ""),
        "courier": str(tracking.get("courier") or tracking.get("carrier") or ""),
        "status": status,
        "status_category": categorize_shipping_status(status),
        "delivered_at": _parse_datetime(tracking.get("delivered_at") or tracking.get("delivery_time")),
        "delivery_latitude": latitude,
        "delivery_longitude": longitude,
        "signature_obtained": _bool_from_any(proof.get("signature_obtained")),
        "delivery_photo_url": proof.get("photo_url"),
        "raw": {
            "source": source,
            "tracking": tracking,
        },
    }


def _normalize_shiprocket(raw: dict[str, Any], tracking_id: str) -> dict[str, Any]:
    tracking_data = raw.get("tracking_data") or raw
    shipments = tracking_data.get("shipment_track") or []
    shipment = shipments[0] if shipments else tracking_data
    activities = tracking_data.get("shipment_track_activities") or []
    delivered_event = next(
        (
            event
            for event in reversed(activities)
            if str(event.get("activity") or event.get("status") or "").upper() == "DELIVERED"
        ),
        {},
    )

    return {
        "tracking_id": shipment.get("awb_code") or shipment.get("awb") or tracking_id,
        "courier": shipment.get("courier_name") or tracking_data.get("courier_name") or "Shiprocket",
        "status": shipment.get("current_status") or tracking_data.get("track_status") or "UNKNOWN",
        "delivered_at": (
            shipment.get("delivered_date")
            or delivered_event.get("date")
            or delivered_event.get("timestamp")
        ),
        "delivery_location": shipment.get("delivery_location") or {},
        "proof_of_delivery": {
            "signature_obtained": bool(
                shipment.get("pod_status") or tracking_data.get("pod_status")
            ),
            "photo_url": shipment.get("pod") or tracking_data.get("pod"),
        },
        "events": activities,
        "provider_raw": raw,
    }


def _normalize_delhivery(raw: dict[str, Any], tracking_id: str) -> dict[str, Any]:
    shipment_data = raw.get("ShipmentData") or []
    shipment_wrapper = shipment_data[0] if shipment_data else {}
    shipment = shipment_wrapper.get("Shipment") or shipment_wrapper
    status_data = shipment.get("Status") or {}
    scans = shipment.get("Scans") or []

    return {
        "tracking_id": shipment.get("AWB") or shipment.get("Waybill") or tracking_id,
        "courier": "Delhivery",
        "status": status_data.get("Status") or shipment.get("StatusType") or "UNKNOWN",
        "delivered_at": status_data.get("StatusDateTime"),
        "delivery_location": shipment.get("DeliveryLocation") or {},
        "proof_of_delivery": {
            "signature_obtained": bool(
                shipment.get("POD") or shipment.get("ProofOfDelivery")
            ),
            "photo_url": shipment.get("PODLink") or shipment.get("DeliveryProof"),
        },
        "events": scans,
        "provider_raw": raw,
    }


def _collect_shiprocket(state: ChargebackState) -> dict[str, Any]:
    tracking_id = state.get("tracking_id")
    if not tracking_id:
        raise ValueError("Shiprocket collection requires tracking_id")
    raw = ShiprocketClient.from_env().get_tracking(tracking_id)
    return _normalize_shiprocket(raw, tracking_id)


def _collect_delhivery(state: ChargebackState) -> dict[str, Any]:
    tracking_id = state.get("tracking_id")
    if not tracking_id:
        raise ValueError("Delhivery collection requires tracking_id")
    raw = DelhiveryClient.from_env().get_tracking(tracking_id)
    return _normalize_delhivery(raw, tracking_id)


def _collect_shipping_data(state: ChargebackState) -> tuple[dict[str, Any], str]:
    if _env_flag("CHARGEGUARD_USE_STUBS"):
        return _stub_tracking_response(state), "shipping_agent_stub"

    primary = state["merchant_profile"].get("shipping_provider", "shiprocket")
    collectors = {
        "shiprocket": _collect_shiprocket,
        "delhivery": _collect_delhivery,
    }
    if primary not in collectors:
        raise ValueError(f"Unsupported shipping provider: {primary}")

    fallback = "delhivery" if primary == "shiprocket" else "shiprocket"
    try:
        return collectors[primary](state), primary
    except Exception as primary_error:
        logger.warning("%s shipping collection failed: %s", primary, primary_error)
        try:
            return collectors[fallback](state), fallback
        except Exception as fallback_error:
            raise ShippingCollectionError(
                f"{primary} failed: {primary_error}; {fallback} failed: {fallback_error}"
            ) from fallback_error


def shipping_agent(state: ChargebackState) -> ChargebackState:
    logger.info("Running shipping agent")

    try:
        tracking, source = _collect_shipping_data(state)
        state["shipping"] = _build_shipping_evidence(tracking, source=source)
    except Exception as exc:
        logger.exception("Shipping evidence collection failed")
        state["shipping"] = _empty_shipping_evidence(state, error=str(exc))

    return state
