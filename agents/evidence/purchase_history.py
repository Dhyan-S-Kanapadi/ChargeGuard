import json
import logging
from datetime import timedelta
from typing import Any

from api.store import store
from core.state import CE3Qualification, ChargebackState, OrderRecord


logger = logging.getLogger(__name__)


def _normalized(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        value = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return " ".join(str(value).strip().casefold().split())


def _matched_elements(disputed: OrderRecord, candidate: OrderRecord) -> list[str]:
    values = {
        "customer_ip": (disputed["customer_ip"], candidate["customer_ip"]),
        "user_agent": (disputed["user_agent"], candidate["user_agent"]),
        "shipping_address": (
            disputed["shipping_address"],
            candidate["shipping_address"],
        ),
        "customer_email": (disputed["customer_email"], candidate["customer_email"]),
    }
    return [
        name
        for name, (current, prior) in values.items()
        if _normalized(current) and _normalized(current) == _normalized(prior)
    ]


def check_ce3_qualification(state: ChargebackState) -> CE3Qualification:
    merchant_id = state["merchant_profile"]["merchant_id"]
    order_id = state.get("commerce_order_id") or state.get("order_id")
    disputed = store.get_order(merchant_id, order_id) if order_id else None
    if disputed is None or not disputed["customer_email"]:
        return {
            "qualifies": False,
            "matched_elements": [],
            "prior_transaction_refs": [],
            "reason": "insufficient_history",
        }

    state["disputed_order_ip"] = disputed["customer_ip"]
    state["disputed_order_user_agent"] = disputed["user_agent"]
    candidates = store.query_orders(
        merchant_id=merchant_id,
        customer_email=disputed["customer_email"],
        start=disputed["order_date"] - timedelta(days=365),
        end=disputed["order_date"] - timedelta(days=120),
        exclude_order_id=disputed["order_id"],
    )
    qualified: list[tuple[str, list[str]]] = []
    for candidate in candidates:
        if candidate["is_disputed"] or candidate["is_fraud_flagged"]:
            continue
        matched = _matched_elements(disputed, candidate)
        if "customer_ip" in matched and len(matched) >= 2:
            qualified.append((candidate["order_id"], matched))

    if len(qualified) < 2:
        return {
            "qualifies": False,
            "matched_elements": [],
            "prior_transaction_refs": [],
            "reason": "insufficient_history",
        }
    return {
        "qualifies": True,
        "matched_elements": sorted({item for _, matches in qualified for item in matches}),
        "prior_transaction_refs": [order_id for order_id, _ in qualified],
        "reason": "qualified",
    }


def purchase_history_agent(state: ChargebackState) -> ChargebackState:
    logger.info("Running CE3.0 purchase-history agent for %s", state["chargeback_id"])
    qualification = check_ce3_qualification(state)
    state["ce3_qualification"] = qualification
    state["compelling_evidence_3_0"] = {
        "qualifies": qualification["qualifies"],
        "matched_fields": qualification["matched_elements"],
        "matched_transactions": [
            {"transaction_id": reference}
            for reference in qualification["prior_transaction_refs"]
        ],
        "reason": qualification["reason"],
    }
    return state
