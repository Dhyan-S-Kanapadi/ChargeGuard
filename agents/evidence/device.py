import logging
import math
import os
from typing import Any

from core.state import ChargebackState, DeviceEvidence
from integrations.seon import SeonClient, SeonConfigError


logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _transaction_context(state: ChargebackState) -> dict[str, Any]:
    transaction = state.get("transaction") or {}
    return {
        "device_id": transaction.get("device_id") or f"device_{state['chargeback_id']}",
        "ip_address": transaction.get("ip_address", ""),
        "customer_email": transaction.get("customer_email", ""),
    }


def _device_use_stubs() -> bool:
    value = os.getenv("SEON_USE_STUBS")
    if value is None:
        return _env_flag("CHARGEGUARD_USE_STUBS")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _stub_device_risk_response(state: ChargebackState) -> dict[str, Any]:
    context = _transaction_context(state)
    return {
        "fraud_score": 18.0,
        "device_fingerprint": context["device_id"],
        "ip_address": context["ip_address"],
        "geo": {"matches_shipping_region": True},
        "login": {"pattern": "normal"},
        "network": {"vpn": False},
    }


def _nested_data(risk: dict[str, Any]) -> dict[str, Any]:
    data = risk.get("data")
    return data if isinstance(data, dict) else risk


def _coordinates(data: dict[str, Any]) -> tuple[float | None, float | None]:
    ip_details = data.get("ip_details") or data.get("ip") or {}
    location = ip_details.get("location") or ip_details.get("geo") or ip_details
    latitude = location.get("latitude") or location.get("lat")
    longitude = location.get("longitude") or location.get("lng") or location.get("lon")
    try:
        return (
            float(latitude) if latitude is not None else None,
            float(longitude) if longitude is not None else None,
        )
    except (TypeError, ValueError):
        return None, None


def _distance_km(
    first: tuple[float, float],
    second: tuple[float, float],
) -> float:
    lat1, lon1 = map(math.radians, first)
    lat2, lon2 = map(math.radians, second)
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 6_371.0 * 2 * math.asin(math.sqrt(haversine))


def _geolocation_match(state: ChargebackState | None, data: dict[str, Any]) -> bool:
    geo = data.get("geo") or {}
    explicit = geo.get("matches_shipping_region")
    if explicit is None:
        explicit = data.get("geolocation_match")
    if explicit is not None:
        return bool(explicit)

    if not state or not state.get("shipping"):
        return False
    shipping = state["shipping"]
    delivery_coordinates = (
        shipping.get("delivery_latitude"),
        shipping.get("delivery_longitude"),
    )
    ip_coordinates = _coordinates(data)
    if None in delivery_coordinates or None in ip_coordinates:
        return False

    threshold = float(os.getenv("DEVICE_GEO_MATCH_KM", "100"))
    return _distance_km(ip_coordinates, delivery_coordinates) <= threshold


def _build_device_evidence(
    risk: dict[str, Any],
    *,
    state: ChargebackState | None = None,
    source: str = "device_agent_stub",
) -> DeviceEvidence:
    data = _nested_data(risk)
    geo = data.get("geo") or {}
    login = data.get("login") or data.get("behavior") or {}
    network = data.get("network") or data.get("ip_details") or {}
    device = data.get("device_details") or {}
    fallback_device = _transaction_context(state)["device_id"] if state else ""

    score = data.get("fraud_score")
    if score is None:
        score = data.get("score", 0.0)
    vpn = network.get("vpn")
    if vpn is None:
        vpn = network.get("is_vpn")
    if vpn is None:
        vpn = data.get("vpn_detected", False)

    return {
        "fraud_score": min(max(float(score), 0.0), 100.0),
        "device_fingerprint": str(
            data.get("device_fingerprint")
            or data.get("fingerprint")
            or data.get("device_id")
            or device.get("device_hash")
            or device.get("session_id")
            or fallback_device
        ),
        "geolocation_match": _geolocation_match(state, data),
        "login_pattern_normal": (
            login.get("pattern") == "normal"
            or bool(data.get("login_pattern_normal"))
            or bool(login.get("is_normal"))
        ),
        "vpn_detected": bool(vpn),
        "raw": {"source": source, "risk": risk},
    }


def _collect_seon(state: ChargebackState) -> dict[str, Any]:
    context = _transaction_context(state)
    if not context["ip_address"]:
        raise ValueError("SEON collection requires transaction IP address")
    payload = {
        "transaction_id": state["chargeback_id"],
        "ip": context["ip_address"],
        "device_id": context["device_id"],
        "email": context["customer_email"],
        "amount": state["dispute_amount"],
        "currency": state["currency"],
    }
    response = SeonClient.from_env().fraud_check(payload)
    if response.get("success") is False:
        raise RuntimeError(f"SEON rejected fraud check: {response.get('error', 'unknown error')}")
    return response


def _collect_device_data(state: ChargebackState) -> tuple[dict[str, Any], str]:
    if _device_use_stubs():
        return _stub_device_risk_response(state), "device_agent_stub"
    return _collect_seon(state), "seon"


def device_agent(state: ChargebackState) -> ChargebackState:
    """Collect device and fraud-risk signals for the disputed payment."""
    logger.info("Running device evidence agent for %s", state["chargeback_id"])
    try:
        risk, source = _collect_device_data(state)
        state["device"] = _build_device_evidence(risk, state=state, source=source)
    except SeonConfigError as exc:
        logger.warning("SEON credentials are unavailable: %s", exc)
        state["device"] = None
        state["evidence_collection_degraded"] = True
        degraded_reasons = state.setdefault("degraded_reasons", [])
        if "seon_credentials_missing" not in degraded_reasons:
            degraded_reasons.append("seon_credentials_missing")
    except Exception as exc:
        logger.exception("Device evidence collection failed: %s", exc)
        state["device"] = None
        state["evidence_collection_degraded"] = True
        degraded_reasons = state.setdefault("degraded_reasons", [])
        if "device" not in degraded_reasons:
            degraded_reasons.append("device")
    return state
