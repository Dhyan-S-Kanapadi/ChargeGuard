import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from core.state import ChargebackState, OrderTimelineEvidence


logger = logging.getLogger(__name__)


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            logger.warning("Unable to parse order timeline timestamp %r", value)
            return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _empty_order_timeline_evidence(
    state: ChargebackState,
    *,
    error: str | None = None,
) -> OrderTimelineEvidence:
    placed_at = state["filing_deadline"] - timedelta(days=18)
    return {
        "placed_at": placed_at,
        "accepted_at": None,
        "picked_at": None,
        "delivered_at": None,
        "post_delivery_rating": None,
        "raw": {
            "source": "order_timeline_agent_empty",
            "error": error,
            "order_id": state.get("order_id", ""),
        },
    }


def _stub_order_timeline_response(state: ChargebackState) -> dict[str, Any]:
    delivered_at = None
    if state.get("shipping"):
        delivered_at = state["shipping"]["delivered_at"]

    placed_at = state["filing_deadline"] - timedelta(days=18)
    accepted_at = placed_at + timedelta(minutes=3)
    picked_at = placed_at + timedelta(days=1)

    return {
        "order_id": state.get("order_id", ""),
        "placed_at": placed_at,
        "accepted_at": accepted_at,
        "picked_at": picked_at,
        "delivered_at": delivered_at,
        "post_delivery_rating": 4.5 if delivered_at else None,
    }


def _build_order_timeline_evidence(response: dict[str, Any]) -> OrderTimelineEvidence:
    evidence: OrderTimelineEvidence = {
        "placed_at": _parse_datetime(response.get("placed_at")) or datetime.now(timezone.utc),
        "accepted_at": _parse_datetime(response.get("accepted_at")),
        "picked_at": _parse_datetime(response.get("picked_at") or response.get("picked_up_at")),
        "delivered_at": _parse_datetime(response.get("delivered_at")),
        "post_delivery_rating": response.get("post_delivery_rating") or response.get("rating"),
        "raw": {
            "source": "order_timeline_agent_stub",
            "response": response,
        },
    }
    return evidence


def order_timeline_agent(state: ChargebackState) -> ChargebackState:
    """Build order lifecycle evidence for delivery-sensitive disputes."""
    logger.info("Running order timeline evidence agent for %s", state["chargeback_id"])

    try:
        response = _stub_order_timeline_response(state)
        state["order_timeline"] = _build_order_timeline_evidence(response)
    except Exception as exc:
        logger.exception("Order timeline evidence collection failed")
        state["order_timeline"] = _empty_order_timeline_evidence(state, error=str(exc))

    return state
