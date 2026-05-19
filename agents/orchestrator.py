import logging
from datetime import datetime, timezone

from core.state import ChargebackState


logger = logging.getLogger(__name__)


FOOD_REASON_CODES = {"13.1", "13.3", "4853"}
FOOD_VERTICALS = {"food_delivery", "quick_commerce"}


def orchestrator_agent(state: ChargebackState) -> ChargebackState:
    """Create the investigation plan and select vertical-specific evidence."""
    logger.info("Running orchestrator agent for %s", state["chargeback_id"])

    vertical = state["merchant_profile"]["vertical"]
    reason_code = state["reason_code"]
    requires_food_agents = vertical in FOOD_VERTICALS or reason_code in FOOD_REASON_CODES

    evidence_tasks = ["transaction", "shipping", "device", "comms", "consortium"]
    if requires_food_agents:
        evidence_tasks.extend(["delivery_photo", "order_timeline"])

    now = datetime.now(timezone.utc)
    days_until_deadline = (state["filing_deadline"] - now).days

    state["requires_food_agents"] = requires_food_agents
    state["investigation_plan"] = {
        "chargeback_id": state["chargeback_id"],
        "reason_code": reason_code,
        "card_network": state["card_network"],
        "vertical": vertical,
        "evidence_tasks": evidence_tasks,
        "days_until_deadline": days_until_deadline,
        "priority": "high" if days_until_deadline <= 7 else "normal",
    }
    return state
