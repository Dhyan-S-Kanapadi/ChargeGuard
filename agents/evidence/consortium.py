import logging
import os
from typing import Any

from core.state import ChargebackState, ConsortiumEvidence
from integrations.ethoca import EthocaClient
from integrations.verifi import VerifiClient


logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _empty_consortium_evidence(
    state: ChargebackState,
    *,
    error: str | None = None,
) -> ConsortiumEvidence:
    return {
        "lookup_complete": False,
        "ethoca_match": False,
        "verifi_match": False,
        "cross_merchant_fraud_history": False,
        "dispute_count_across_merchants": 0,
        "raw": {
            "source": "consortium_agent_empty",
            "error": error,
            "card_network": state["card_network"],
        },
    }


def _stub_consortium_response(state: ChargebackState) -> dict[str, Any]:
    return {
        "lookup_complete": True,
        "ethoca": {"match": False},
        "verifi": {"match": False},
        "history": {
            "cross_merchant_fraud_history": False,
            "dispute_count_across_merchants": 0,
        },
        "source_errors": {},
        "card_network": state["card_network"],
    }


def _match_and_count(response: dict[str, Any]) -> tuple[bool, int]:
    data = response.get("data") if isinstance(response.get("data"), dict) else response
    alerts = data.get("alerts") or data.get("matches") or []
    match = bool(
        data.get("match")
        or data.get("matched")
        or data.get("has_alert")
        or alerts
    )
    count_value = (
        data.get("dispute_count_across_merchants")
        or data.get("dispute_count")
        or data.get("alert_count")
    )
    count = int(count_value) if count_value is not None else len(alerts)
    return match, count


def _build_consortium_evidence(response: dict[str, Any]) -> ConsortiumEvidence:
    ethoca = response.get("ethoca") or {}
    verifi = response.get("verifi") or {}
    history = response.get("history") or {}
    ethoca_match, ethoca_count = _match_and_count(ethoca)
    verifi_match, verifi_count = _match_and_count(verifi)
    dispute_count = int(
        history.get("dispute_count_across_merchants")
        or response.get("dispute_count_across_merchants")
        or max(ethoca_count, verifi_count)
        or 0
    )

    return {
        "lookup_complete": bool(response.get("lookup_complete", True)),
        "ethoca_match": bool(ethoca_match or response.get("ethoca_match")),
        "verifi_match": bool(verifi_match or response.get("verifi_match")),
        "cross_merchant_fraud_history": bool(
            history.get("cross_merchant_fraud_history")
            or response.get("cross_merchant_fraud_history")
            or dispute_count > 1
        ),
        "dispute_count_across_merchants": dispute_count,
        "raw": {
            "source": "ethoca_verifi",
            "response": response,
        },
    }


def _lookup_identifiers(state: ChargebackState) -> dict[str, str]:
    transaction = state.get("transaction") or {}
    identifiers = {
        "card_fingerprint": state.get("card_fingerprint", ""),
        "customer_email": transaction.get("customer_email", ""),
        "payment_id": transaction.get("payment_id") or state.get("payment_id", ""),
        "merchant_id": state["merchant_profile"]["merchant_id"],
    }
    filtered = {key: str(value) for key, value in identifiers.items() if value}
    if not any(key in filtered for key in ("card_fingerprint", "customer_email", "payment_id")):
        raise ValueError("Consortium lookup requires a card fingerprint, email, or payment ID")
    return filtered


def _collect_consortium_data(state: ChargebackState) -> dict[str, Any]:
    if _env_flag("CHARGEGUARD_USE_STUBS"):
        return _stub_consortium_response(state)

    identifiers = _lookup_identifiers(state)
    errors: dict[str, str] = {}
    completed = 0
    try:
        ethoca = EthocaClient.from_env().search_alerts(identifiers)
        completed += 1
    except Exception as exc:
        logger.warning("Ethoca lookup failed: %s", exc)
        ethoca = {}
        errors["ethoca"] = str(exc)

    try:
        verifi = VerifiClient.from_env().search_alerts(identifiers)
        completed += 1
    except Exception as exc:
        logger.warning("Verifi lookup failed: %s", exc)
        verifi = {}
        errors["verifi"] = str(exc)

    return {
        "lookup_complete": completed == 2,
        "ethoca": ethoca,
        "verifi": verifi,
        "source_errors": errors,
    }


def consortium_agent(state: ChargebackState) -> ChargebackState:
    """Collect Ethoca and Verifi dispute intelligence independently."""
    logger.info("Running consortium evidence agent for %s", state["chargeback_id"])
    try:
        response = _collect_consortium_data(state)
        state["consortium"] = _build_consortium_evidence(response)
    except Exception as exc:
        logger.exception("Consortium evidence collection failed")
        state["consortium"] = _empty_consortium_evidence(state, error=str(exc))
    return state
