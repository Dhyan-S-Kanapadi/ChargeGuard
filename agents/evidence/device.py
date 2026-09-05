import logging
import math
import os
from ipaddress import ip_address
from typing import Any

import httpx

from api.store import store
from api.simulation_scenarios import get_simulation_scenario, simulation_record_for_state
from core.state import ChargebackState, DeviceEvidence
from integrations.credential_secrets import (
    CredentialStoreError,
    credential_secret_store_from_env,
)
from integrations.device_risk_client_factory import (
    DeviceRiskClientFactory,
    DeviceRiskConnectorError,
)
from integrations.seon import SeonConfigError, SeonRequestError


logger = logging.getLogger(__name__)
device_risk_client_factory = DeviceRiskClientFactory(store)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _transaction_context(state: ChargebackState) -> dict[str, Any]:
    transaction = state.get("transaction") or {}
    return {
        "device_id": transaction.get("device_id") or "",
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
    record = simulation_record_for_state(state)
    scenario = get_simulation_scenario(record.get("scenario_id", "")) if record else None
    profile = scenario.get("device") if scenario else None
    if profile:
        ip_address(context["ip_address"])
        if profile.get("error") == "timeout":
            raise httpx.ReadTimeout("Simulated device timeout")
        if profile.get("error") == "authentication":
            raise SeonRequestError("simulated_authentication_failure", status_code=401)
        return profile["risk"]
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
    latitude = location.get("latitude", location.get("lat"))
    longitude = location.get("longitude", location.get("lng", location.get("lon")))
    try:
        coordinates = (
            float(latitude) if latitude is not None else None,
            float(longitude) if longitude is not None else None,
        )
        if coordinates[0] is None or coordinates[1] is None:
            return None, None
        if not (-90 <= coordinates[0] <= 90 and -180 <= coordinates[1] <= 180):
            raise ValueError("Invalid IP coordinates")
        return coordinates
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
    return 6_371.0 * 2 * math.asin(math.sqrt(min(1.0, max(0.0, haversine))))


def _signal_bool(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in {"true", "false", "0", "1"}:
        return value.strip().lower() in {"true", "1"}
    raise ValueError("Invalid boolean risk signal")


def _geolocation_match(state: ChargebackState | None, data: dict[str, Any]) -> bool:
    geo = data.get("geo") or {}
    explicit = geo.get("matches_shipping_region")
    if explicit is None:
        explicit = data.get("geolocation_match")
    if explicit is not None:
        return _signal_bool(explicit)

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
    login = data.get("login") or data.get("behavior") or {}
    network = data.get("network") or data.get("ip_details") or {}
    device = data.get("device_details") or {}
    fallback_device = _transaction_context(state)["device_id"] if state else ""

    score = data.get("fraud_score")
    if score is None:
        score = data.get("score")
    score = float(score)
    if not math.isfinite(score):
        raise ValueError("Invalid fraud score")
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
            or _signal_bool(data.get("login_pattern_normal"))
            or _signal_bool(login.get("is_normal"))
        ),
        "vpn_detected": _signal_bool(vpn),
        "raw": {"source": source},
    }


def _record_seon_success(state: ChargebackState) -> None:
    merchant = state["merchant_profile"]
    pending = next(
        (
            item
            for item in store.list_device_risk_connectors(merchant["merchant_id"])
            if item["status"] == "verification_pending"
        ),
        None,
    )
    connector_id = (
        pending["connector_id"]
        if pending
        else merchant.get("device_risk_connector_id")
    )
    if not connector_id:
        return
    activated = store.activate_device_risk_connector(merchant["merchant_id"], connector_id)
    if activated is None:
        return
    _, previous_id = activated
    if previous_id:
        try:
            credential_secret_store_from_env().delete(previous_id)
        except CredentialStoreError as exc:
            logger.error(
                "Unable to remove rotated device-risk connector secret",
                extra={
                    "merchant_id": merchant["merchant_id"],
                    "connector_id": previous_id,
                    "error_code": exc.code,
                },
            )


def _record_seon_failure(
    state: ChargebackState,
    error_code: str,
    *,
    invalid: bool,
) -> None:
    merchant = state["merchant_profile"]
    pending = next(
        (
            item
            for item in store.list_device_risk_connectors(merchant["merchant_id"])
            if item["status"] == "verification_pending"
        ),
        None,
    )
    connector_id = (
        pending["connector_id"]
        if pending
        else merchant.get("device_risk_connector_id")
    )
    if not connector_id:
        return
    store.update_device_risk_connector_status(
        merchant["merchant_id"],
        connector_id,
        status="invalid" if invalid else None,
        last_error_code=error_code,
        audit_action="authentication_failed" if invalid else "request_failed",
    )
    if invalid:
        try:
            credential_secret_store_from_env().delete(connector_id)
        except CredentialStoreError as exc:
            logger.error(
                "Unable to remove invalid device-risk connector secret",
                extra={
                    "merchant_id": merchant["merchant_id"],
                    "connector_id": connector_id,
                    "error_code": exc.code,
                },
            )


def _collect_seon(state: ChargebackState) -> dict[str, Any]:
    context = _transaction_context(state)
    if not context["ip_address"]:
        raise ValueError("SEON collection requires transaction IP address")
    ip_address(context["ip_address"])
    payload = {
        "transaction_id": state["chargeback_id"],
        "ip": context["ip_address"],
        "device_id": context["device_id"],
        "email": context["customer_email"],
        "amount": state["dispute_amount"],
        "currency": state["currency"],
    }
    try:
        response = device_risk_client_factory.for_merchant(
            state["merchant_profile"]
        ).fraud_check(payload)
    except SeonRequestError as exc:
        _record_seon_failure(
            state,
            "provider_authentication_failed"
            if exc.status_code in {401, 403}
            else "provider_request_failed",
            invalid=exc.status_code in {401, 403},
        )
        raise
    except httpx.TimeoutException:
        _record_seon_failure(state, "provider_timeout", invalid=False)
        raise
    except httpx.HTTPError:
        _record_seon_failure(state, "provider_unavailable", invalid=False)
        raise
    if response.get("success") is False:
        _record_seon_failure(state, "provider_response_unsuccessful", invalid=False)
        raise SeonRequestError("seon_response_unsuccessful")
    _record_seon_success(state)
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
    except (SeonConfigError, DeviceRiskConnectorError, CredentialStoreError):
        logger.warning("SEON credentials are unavailable")
        state["device"] = None
        state["evidence_collection_degraded"] = True
        degraded_reasons = state.setdefault("degraded_reasons", [])
        if "seon_credentials_missing" not in degraded_reasons:
            degraded_reasons.append("seon_credentials_missing")
    except Exception:
        logger.error("Device evidence collection failed")
        state["device"] = None
        state["evidence_collection_degraded"] = True
        degraded_reasons = state.setdefault("degraded_reasons", [])
        if "device_provider_unavailable" not in degraded_reasons:
            degraded_reasons.append("device_provider_unavailable")
    return state
