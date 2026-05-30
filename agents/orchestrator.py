import logging
from datetime import datetime, timezone

from core.state import ChargebackState


logger = logging.getLogger(__name__)


FOOD_REASON_CODES = {"13.1", "13.3", "4853"}
FOOD_VERTICALS = {"food_delivery", "quick_commerce"}


def _deadline_priority(days_until_deadline: int) -> str:
    if days_until_deadline < 0:
        return "overdue"
    if days_until_deadline <= 3:
        return "urgent"
    if days_until_deadline <= 7:
        return "high"
    return "normal"


def _requires_food_agents(state: ChargebackState) -> bool:
    vertical = state["merchant_profile"]["vertical"]
    reason_code = state["reason_code"]
    return vertical in FOOD_VERTICALS or reason_code in FOOD_REASON_CODES


def _evidence_tasks(requires_food_agents: bool) -> list[str]:
    tasks = ["transaction", "shipping", "device", "comms", "consortium"]
    if requires_food_agents:
        tasks.extend(["delivery_photo", "order_timeline"])
    return tasks


def _build_investigation_plan(
    state: ChargebackState,
    *,
    now: datetime | None = None,
) -> dict:
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    deadline = state["filing_deadline"]
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)

    requires_food_agents = _requires_food_agents(state)
    days_until_deadline = (deadline - current_time).days
    return {
        "chargeback_id": state["chargeback_id"],
        "reason_code": state["reason_code"],
        "card_network": state["card_network"],
        "vertical": state["merchant_profile"]["vertical"],
        "evidence_tasks": _evidence_tasks(requires_food_agents),
        "days_until_deadline": days_until_deadline,
        "priority": _deadline_priority(days_until_deadline),
        "routing": {
            "food_evidence": requires_food_agents,
            "standard_evidence": True,
        },
    }


def orchestrator_agent(state: ChargebackState) -> ChargebackState:
    """Create the investigation plan and select vertical-specific evidence."""
    logger.info("Running orchestrator agent for %s", state["chargeback_id"])

    requires_food_agents = _requires_food_agents(state)
    state["requires_food_agents"] = requires_food_agents
    state["investigation_plan"] = _build_investigation_plan(state)
    return state
