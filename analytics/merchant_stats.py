import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from core.state import MerchantProfile


logger = logging.getLogger(__name__)
MONITORING_WINDOW_DAYS = 30


def _threshold_for_network(card_network: str) -> float | None:
    env_name = f"{card_network.upper()}_DISPUTE_RATIO_THRESHOLD_PCT"
    raw_value = os.getenv(env_name)
    if not raw_value:
        return None
    try:
        threshold = float(raw_value)
    except ValueError:
        logger.warning("Invalid %s=%r; monitoring threshold is unconfigured", env_name, raw_value)
        return None
    if threshold < 0:
        logger.warning("Invalid negative %s=%r; monitoring threshold is unconfigured", env_name, raw_value)
        return None
    return threshold


def merchant_dispute_ratio(
    merchant: MerchantProfile,
    disputes: list[dict[str, Any]],
    card_network: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a network-specific 30-day monitoring ratio without affecting scoring."""
    network = card_network.upper()
    current_time = now or datetime.now(timezone.utc)
    window_start = current_time - timedelta(days=MONITORING_WINDOW_DAYS)
    dispute_count = sum(
        1
        for record in disputes
        if record.get("created_at") is not None
        and record["created_at"] >= window_start
        and record.get("state", {}).get("merchant_profile", {}).get("merchant_id")
        == merchant["merchant_id"]
        and record.get("state", {}).get("card_network") == network
    )
    volume_by_network = merchant.get("transaction_volume_30d_by_network", {})
    transaction_count = volume_by_network.get(network)
    threshold = _threshold_for_network(network)

    if transaction_count is None or transaction_count <= 0:
        ratio = None
        status = "UNAVAILABLE"
    else:
        ratio = round((dispute_count / transaction_count) * 100, 4)
        if threshold is None:
            status = "UNCONFIGURED"
        else:
            status = "WARNING" if ratio >= threshold else "OK"

    return {
        "window_days": MONITORING_WINDOW_DAYS,
        "card_network": network,
        "dispute_count": dispute_count,
        "transaction_count": transaction_count,
        "current_ratio_pct": ratio,
        "threshold_pct": threshold,
        "status": status,
    }
