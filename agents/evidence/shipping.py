"""Read-only, merchant-scoped shipping evidence collection."""

from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from api.store import store
from core.runtime import evidence_uses_stubs
from core.shipping_status import categorize_shipping_status
from core.state import ChargebackState, ShippingEvidence
from integrations.shipping_client_factory import ShippingClientFactory, ShippingConnectorError


logger = logging.getLogger(__name__)
shipping_client_factory = ShippingClientFactory(store)


def _bool_from_any(value: Any) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "signed", "obtained"} if isinstance(value, str) else bool(value)


def _empty_shipping_evidence(state: ChargebackState, *, error: str | None = None) -> ShippingEvidence:
    return {"tracking_id": state.get("tracking_id", ""), "courier": "", "status": "UNKNOWN",
            "status_category": "UNKNOWN", "delivered_at": None, "delivery_latitude": None,
            "delivery_longitude": None, "signature_obtained": False, "delivery_photo_url": None,
            "raw": {"source": "shipping_agent_empty", "error": error}}


def _stub_tracking_response(state: ChargebackState, provider: str) -> dict[str, Any]:
    tracking_id = state.get("tracking_id") or f"trk_{state.get('order_id', state['chargeback_id'])}"
    delivered_at = state["filing_deadline"] - timedelta(days=12)
    return {"tracking_id": tracking_id, "courier": provider.title(), "status": "DELIVERED",
            "delivered_at": delivered_at.isoformat(), "delivery_location": {"latitude": 12.9716, "longitude": 77.5946},
            "proof_of_delivery": {"signature_obtained": True, "photo_url": f"https://example.test/pod/{tracking_id}.jpg"}}


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)


def _build_shipping_evidence(tracking: dict[str, Any], *, source: str = "shipping_agent_stub") -> ShippingEvidence:
    location = tracking.get("delivery_location") or tracking.get("location") or {}
    gps = tracking.get("gps") or {}
    proof = tracking.get("proof_of_delivery") or tracking.get("pod") or {}
    status = str(tracking.get("status") or tracking.get("current_status") or "UNKNOWN")
    return {"tracking_id": str(tracking.get("tracking_id") or tracking.get("awb") or tracking.get("waybill") or ""),
            "courier": str(tracking.get("courier") or tracking.get("carrier") or ""), "status": status,
            "status_category": categorize_shipping_status(status),
            "delivered_at": _parse_datetime(tracking.get("delivered_at") or tracking.get("delivery_time")),
            "delivery_latitude": location.get("latitude") or location.get("lat") or gps.get("latitude") or gps.get("lat"),
            "delivery_longitude": location.get("longitude") or location.get("lng") or location.get("lon") or gps.get("longitude") or gps.get("lng") or gps.get("lon"),
            "signature_obtained": _bool_from_any(proof.get("signature_obtained") or proof.get("signature")),
            "delivery_photo_url": proof.get("photo_url") or proof.get("image_url"),
            "raw": {"source": source}}


def _normalize_shiprocket(raw: dict[str, Any], tracking_id: str) -> dict[str, Any]:
    data = raw.get("tracking_data") if isinstance(raw.get("tracking_data"), dict) else raw
    shipments = data.get("shipment_track") if isinstance(data, dict) else []
    shipment = shipments[0] if isinstance(shipments, list) and shipments else data
    activities = data.get("shipment_track_activities") if isinstance(data, dict) else []
    delivered = next((event for event in reversed(activities or []) if isinstance(event, dict)
                      and str(event.get("activity") or event.get("status") or "").upper() == "DELIVERED"), {})
    return {"tracking_id": shipment.get("awb_code") or shipment.get("awb") or tracking_id,
            "courier": shipment.get("courier_name") or data.get("courier_name") or "Shiprocket",
            "status": shipment.get("current_status") or data.get("track_status") or "UNKNOWN",
            "delivered_at": shipment.get("delivered_date") or delivered.get("date") or delivered.get("timestamp"),
            "delivery_location": shipment.get("delivery_location") or {},
            "proof_of_delivery": {"signature_obtained": bool(shipment.get("pod_status") or data.get("pod_status")),
                                  "photo_url": shipment.get("pod") or data.get("pod")}}


def _normalize_delhivery(raw: dict[str, Any], tracking_id: str) -> dict[str, Any]:
    entries = raw.get("ShipmentData") or []
    wrapper = entries[0] if isinstance(entries, list) and entries else {}
    shipment = wrapper.get("Shipment") if isinstance(wrapper, dict) else {}
    status = shipment.get("Status") or {}
    return {"tracking_id": shipment.get("AWB") or shipment.get("Waybill") or tracking_id,
            "courier": "Delhivery", "status": status.get("Status") or shipment.get("StatusType") or "UNKNOWN",
            "delivered_at": status.get("StatusDateTime"), "delivery_location": shipment.get("DeliveryLocation") or {},
            "proof_of_delivery": {"signature_obtained": bool(shipment.get("POD") or shipment.get("ProofOfDelivery")),
                                  "photo_url": shipment.get("PODLink") or shipment.get("DeliveryProof")}}


def _provider(state: ChargebackState) -> str | None:
    return state.get("shipping_provider") or state["merchant_profile"].get("shipping_provider")


def _collect_shipping_data(state: ChargebackState) -> tuple[dict[str, Any], str]:
    tracking_id = state.get("tracking_id")
    if not tracking_id:
        raise ShippingConnectorError("shipping_tracking_id_unavailable")
    provider = _provider(state)
    if provider not in {"shiprocket", "delhivery"}:
        if evidence_uses_stubs("shiprocket"):
            provider = "shiprocket"
        else:
            raise ShippingConnectorError("shipping_provider_unavailable")
    if evidence_uses_stubs(provider):
        return _stub_tracking_response(state, provider), "shipping_agent_stub"
    raw = shipping_client_factory.for_merchant(state["merchant_profile"], provider).get_tracking(tracking_id)
    return (_normalize_shiprocket(raw, tracking_id) if provider == "shiprocket" else _normalize_delhivery(raw, tracking_id), provider)


def shipping_agent(state: ChargebackState) -> ChargebackState:
    logger.info("Running shipping agent")
    try:
        tracking, source = _collect_shipping_data(state)
        state["shipping"] = _build_shipping_evidence(tracking, source=source)
    except ShippingConnectorError as exc:
        reason = exc.code
        state["shipping"] = _empty_shipping_evidence(state, error=reason)
        state["evidence_collection_degraded"] = True
        if reason not in state.setdefault("degraded_reasons", []):
            state["degraded_reasons"].append(reason)
    except Exception:
        logger.error("Shipping evidence collection failed")
        reason = "shipping_tracking_id_unavailable" if not state.get("tracking_id") else "shipping_provider_unavailable"
        state["shipping"] = _empty_shipping_evidence(state, error=reason)
        state["evidence_collection_degraded"] = True
        if reason not in state.setdefault("degraded_reasons", []):
            state["degraded_reasons"].append(reason)
    return state
