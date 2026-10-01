import logging
from core.runtime import evidence_uses_stubs
import os
from typing import Any

from core.state import ChargebackState, ConsortiumEvidence
from api.store import store
from integrations.consortium_client_factory import ConsortiumClientFactory, ConsortiumConnectorError


logger = logging.getLogger(__name__)
consortium_client_factory = ConsortiumClientFactory(store)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _consortium_use_stubs(provider: str) -> bool:
    return evidence_uses_stubs(provider)


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


def _normalize_provider_response(response: dict[str, Any]) -> dict[str, int | bool]:
    """Keep only deterministic provider signals; never retain provider payloads."""
    data = response.get("data") if isinstance(response.get("data"), dict) else response
    if not isinstance(data, dict):
        return {"match": False, "dispute_count": 0}
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
    try:
        count = max(0, int(count_value)) if count_value is not None else len(alerts)
    except (TypeError, ValueError):
        count = len(alerts)
    return {"match": match, "dispute_count": count}


def _build_consortium_evidence(response: dict[str, Any]) -> ConsortiumEvidence:
    ethoca = _normalize_provider_response(response.get("ethoca") or {})
    verifi = _normalize_provider_response(response.get("verifi") or {})
    history = response.get("history") if isinstance(response.get("history"), dict) else {}
    history_count = _normalize_provider_response(history)["dispute_count"]
    top_level_count = _normalize_provider_response({
        "dispute_count_across_merchants": response.get("dispute_count_across_merchants")
    })["dispute_count"]
    dispute_count = max(
        int(ethoca["dispute_count"]), int(verifi["dispute_count"]),
        int(history_count), int(top_level_count),
    )
    cross_merchant_history = bool(
        history.get("cross_merchant_fraud_history")
        or response.get("cross_merchant_fraud_history")
        or dispute_count > 1
    )

    return {
        "lookup_complete": bool(response.get("lookup_complete", True)),
        "ethoca_match": bool(ethoca["match"] or response.get("ethoca_match")),
        "verifi_match": bool(verifi["match"] or response.get("verifi_match")),
        "cross_merchant_fraud_history": cross_merchant_history,
        "dispute_count_across_merchants": dispute_count,
        "raw": {
            "source": "ethoca_verifi",
            "providers": {"ethoca": ethoca, "verifi": verifi},
            "source_errors": response.get("source_errors", {}),
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
    if _consortium_use_stubs("ethoca") and _consortium_use_stubs("verifi"):
        return _stub_consortium_response(state)

    identifiers = _lookup_identifiers(state)
    errors: dict[str, str] = {}
    degraded_reasons: list[str] = []
    completed = 0
    if _consortium_use_stubs("ethoca"):
        ethoca = {"match": False}
        completed += 1
    else:
        try:
            ethoca = consortium_client_factory.for_merchant(state["merchant_profile"], "ethoca").search_alerts(identifiers)
            completed += 1
        except ConsortiumConnectorError:
            logger.warning("Ethoca credentials are unavailable")
            ethoca = {}
            errors["ethoca"] = "ethoca_credentials_missing"
            degraded_reasons.append("ethoca_credentials_missing")
        except Exception:
            logger.warning("Ethoca lookup failed")
            ethoca = {}
            errors["ethoca"] = "ethoca_provider_unavailable"
            degraded_reasons.append("ethoca_provider_unavailable")

    if _consortium_use_stubs("verifi"):
        verifi = {"match": False}
        completed += 1
    else:
        try:
            verifi = consortium_client_factory.for_merchant(state["merchant_profile"], "verifi").search_alerts(identifiers)
            completed += 1
        except ConsortiumConnectorError:
            logger.warning("Verifi credentials are unavailable")
            verifi = {}
            errors["verifi"] = "verifi_credentials_missing"
            degraded_reasons.append("verifi_credentials_missing")
        except Exception:
            logger.warning("Verifi lookup failed")
            verifi = {}
            errors["verifi"] = "verifi_provider_unavailable"
            degraded_reasons.append("verifi_provider_unavailable")

    return {
        "lookup_complete": completed == 2,
        "ethoca": ethoca,
        "verifi": verifi,
        "source_errors": errors,
        "degraded_reasons": degraded_reasons,
    }


def consortium_agent(state: ChargebackState) -> ChargebackState:
    """Collect Ethoca and Verifi dispute intelligence independently."""
    logger.info("Running consortium evidence agent for %s", state["chargeback_id"])
    try:
        response = _collect_consortium_data(state)
        state["consortium"] = _build_consortium_evidence(response)
        for reason in response.get("degraded_reasons", []):
            state["evidence_collection_degraded"] = True
            degraded_reasons = state.setdefault("degraded_reasons", [])
            if reason not in degraded_reasons:
                degraded_reasons.append(reason)
    except Exception:
        logger.error("Consortium evidence collection failed")
        state["consortium"] = _empty_consortium_evidence(
            state,
            error="consortium_provider_unavailable",
        )
        state["evidence_collection_degraded"] = True
        degraded_reasons = state.setdefault("degraded_reasons", [])
        if "consortium_provider_unavailable" not in degraded_reasons:
            degraded_reasons.append("consortium_provider_unavailable")
    return state
