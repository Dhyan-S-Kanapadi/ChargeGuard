import logging
from datetime import timedelta

from core.state import ChargebackState, OrderTimelineEvidence


logger = logging.getLogger(__name__)


def order_timeline_agent(state: ChargebackState) -> ChargebackState:
    """Build order lifecycle evidence for delivery-sensitive disputes."""
    logger.info("Running order timeline evidence agent for %s", state["chargeback_id"])

    delivered_at = None
    if state.get("shipping"):
        delivered_at = state["shipping"]["delivered_at"]

    placed_at = state["filing_deadline"] - timedelta(days=18)
    accepted_at = placed_at + timedelta(minutes=3)
    picked_at = placed_at + timedelta(days=1)

    evidence: OrderTimelineEvidence = {
        "placed_at": placed_at,
        "accepted_at": accepted_at,
        "picked_at": picked_at,
        "delivered_at": delivered_at,
        "post_delivery_rating": 4.5 if delivered_at else None,
        "raw": {
            "source": "order_timeline_agent_stub",
            "order_id": state.get("order_id", ""),
        },
    }
    state["order_timeline"] = evidence
    return state
