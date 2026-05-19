import logging

from core.state import ChargebackState, DeviceEvidence


logger = logging.getLogger(__name__)


def device_agent(state: ChargebackState) -> ChargebackState:
    """Collect device and risk signals for the disputed payment."""
    logger.info("Running device evidence agent for %s", state["chargeback_id"])

    transaction = state.get("transaction") or {}
    device_id = transaction.get("device_id") or f"device_{state['chargeback_id']}"

    evidence: DeviceEvidence = {
        "fraud_score": 18.0,
        "device_fingerprint": str(device_id),
        "geolocation_match": True,
        "login_pattern_normal": True,
        "vpn_detected": False,
        "raw": {
            "source": "device_agent_stub",
            "ip_address": transaction.get("ip_address", ""),
        },
    }
    state["device"] = evidence
    return state
