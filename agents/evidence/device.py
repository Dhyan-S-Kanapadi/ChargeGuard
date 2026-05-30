import logging
from typing import Any

from core.state import ChargebackState, DeviceEvidence


logger = logging.getLogger(__name__)


def _transaction_context(state: ChargebackState) -> dict:
    transaction = state.get("transaction") or {}
    return {
        "device_id": transaction.get("device_id") or f"device_{state['chargeback_id']}",
        "ip_address": transaction.get("ip_address", ""),
    }


def _empty_device_evidence(
    state: ChargebackState,
    *,
    error: str | None = None,
) -> DeviceEvidence:
    context = _transaction_context(state)
    return {
        "fraud_score": 100.0,
        "device_fingerprint": str(context["device_id"]),
        "geolocation_match": False,
        "login_pattern_normal": False,
        "vpn_detected": True,
        "raw": {
            "source": "device_agent_empty",
            "error": error,
            "ip_address": context["ip_address"],
        },
    }


def _stub_device_risk_response(state: ChargebackState) -> dict[str, Any]:
    context = _transaction_context(state)
    return {
        "fraud_score": 18.0,
        "device_fingerprint": context["device_id"],
        "ip_address": context["ip_address"],
        "geo": {
            "matches_shipping_region": True,
        },
        "login": {
            "pattern": "normal",
        },
        "network": {
            "vpn": False,
        },
    }


def _build_device_evidence(risk: dict[str, Any]) -> DeviceEvidence:
    geo = risk.get("geo") or {}
    login = risk.get("login") or {}
    network = risk.get("network") or {}

    evidence: DeviceEvidence = {
        "fraud_score": float(risk.get("fraud_score") or risk.get("score") or 0.0),
        "device_fingerprint": str(
            risk.get("device_fingerprint") or risk.get("fingerprint") or risk.get("device_id") or ""
        ),
        "geolocation_match": bool(geo.get("matches_shipping_region") or risk.get("geolocation_match")),
        "login_pattern_normal": login.get("pattern") == "normal" or bool(risk.get("login_pattern_normal")),
        "vpn_detected": bool(network.get("vpn") or risk.get("vpn_detected")),
        "raw": {
            "source": "device_agent_stub",
            "risk": risk,
        },
    }
    return evidence


def device_agent(state: ChargebackState) -> ChargebackState:
    """Collect device and risk signals for the disputed payment."""
    logger.info("Running device evidence agent for %s", state["chargeback_id"])

    try:
        risk = _stub_device_risk_response(state)
        state["device"] = _build_device_evidence(risk)
    except Exception as exc:
        logger.exception("Device evidence collection failed")
        state["device"] = _empty_device_evidence(state, error=str(exc))

    return state
