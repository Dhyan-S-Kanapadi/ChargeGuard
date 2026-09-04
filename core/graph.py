import logging
from typing import Literal

from langgraph.graph import END, StateGraph

from agents.acceptance import accept_and_log_agent
from agents.decision_review import decision_review_agent
from agents.evidence.comms import comms_agent
from agents.evidence.consortium import consortium_agent
from agents.evidence.delivery_photo import delivery_photo_agent
from agents.evidence.device import device_agent
from agents.evidence.order_timeline import order_timeline_agent
from agents.evidence.order_correlation import order_correlation_agent
from agents.evidence.purchase_history import purchase_history_agent
from agents.evidence.shipping import shipping_agent
from agents.evidence.transaction import transaction_agent
from agents.escalation import human_escalation_agent
from agents.filing import filing_agent
from agents.learning import learning_agent
from agents.orchestrator import orchestrator_agent
from agents.quality_check import quality_check_agent
from agents.rebuttal_builder import rebuttal_builder_agent
from agents.scoring import scoring_agent
from core.state import ChargebackState, is_filed_dispute


logger = logging.getLogger(__name__)


def route_food_evidence(state: ChargebackState) -> Literal["food", "ce3", "standard"]:
    if state.get("requires_food_agents"):
        return "food"
    if route_ce3_evidence(state) == "ce3":
        return "ce3"
    return "standard"


def route_ce3_evidence(state: ChargebackState) -> Literal["ce3", "standard"]:
    if state.get("card_network") == "VISA" and state.get("reason_code") == "10.4":
        return "ce3"
    return "standard"


def route_priority(state: ChargebackState) -> Literal["full", "expedited"]:
    if state.get("investigation_plan", {}).get("priority") == "overdue":
        return "expedited"
    return "full"


def route_decision(state: ChargebackState) -> Literal["FIGHT", "ACCEPT", "ESCALATE_DEGRADED"]:
    if state.get("decision") == "FIGHT":
        return "FIGHT"
    if state.get("decision") == "ESCALATE_DEGRADED":
        return "ESCALATE_DEGRADED"
    return "ACCEPT"


def route_quality(state: ChargebackState) -> Literal["approved", "retry", "escalate"]:
    if state.get("quality_approved"):
        return "approved"
    if state.get("quality_auto_fixable") is False:
        return "escalate"
    if state.get("quality_loop_count", 0) >= 3:
        return "escalate"
    return "retry"


def route_learning(state: ChargebackState) -> Literal["learn", "end"]:
    if state.get("final_outcome") in {"WIN", "LOSS"} and is_filed_dispute(state):
        return "learn"
    return "end"


def build_graph():
    graph = StateGraph(ChargebackState)

    graph.add_node("orchestrator", orchestrator_agent)
    graph.add_node("transaction_evidence", transaction_agent)
    graph.add_node("shipping_evidence", shipping_agent)
    graph.add_node("order_correlation", order_correlation_agent)
    graph.add_node("expedited_transaction_evidence", transaction_agent)
    graph.add_node("expedited_shipping_evidence", shipping_agent)
    graph.add_node("expedited_order_correlation", order_correlation_agent)
    graph.add_node("device_evidence", device_agent)
    graph.add_node("comms_evidence", comms_agent)
    graph.add_node("consortium_evidence", consortium_agent)
    graph.add_node("delivery_photo_evidence", delivery_photo_agent)
    graph.add_node("order_timeline_evidence", order_timeline_agent)
    graph.add_node("purchase_history_evidence", purchase_history_agent)
    graph.add_node("scoring", scoring_agent)
    graph.add_node("decision_review", decision_review_agent)
    graph.add_node("rebuttal_builder", rebuttal_builder_agent)
    graph.add_node("quality_check", quality_check_agent)
    graph.add_node("filing", filing_agent)
    graph.add_node("learning", learning_agent)
    graph.add_node("accept_and_log", accept_and_log_agent)
    graph.add_node("human_escalation", human_escalation_agent)

    graph.set_entry_point("orchestrator")
    graph.add_conditional_edges(
        "orchestrator",
        route_priority,
        {
            "full": "transaction_evidence",
            "expedited": "expedited_transaction_evidence",
        },
    )
    graph.add_edge("transaction_evidence", "order_correlation")
    graph.add_edge("order_correlation", "shipping_evidence")
    graph.add_edge("shipping_evidence", "device_evidence")
    graph.add_edge("expedited_transaction_evidence", "expedited_order_correlation")
    graph.add_edge("expedited_order_correlation", "expedited_shipping_evidence")
    graph.add_conditional_edges(
        "expedited_shipping_evidence",
        route_ce3_evidence,
        {"ce3": "purchase_history_evidence", "standard": "scoring"},
    )
    graph.add_edge("device_evidence", "comms_evidence")
    graph.add_edge("comms_evidence", "consortium_evidence")
    graph.add_conditional_edges(
        "consortium_evidence",
        route_food_evidence,
        {
            "food": "delivery_photo_evidence",
            "ce3": "purchase_history_evidence",
            "standard": "scoring",
        },
    )
    graph.add_edge("delivery_photo_evidence", "order_timeline_evidence")
    graph.add_conditional_edges(
        "order_timeline_evidence",
        route_ce3_evidence,
        {"ce3": "purchase_history_evidence", "standard": "scoring"},
    )
    graph.add_edge("purchase_history_evidence", "scoring")
    graph.add_edge("scoring", "decision_review")
    graph.add_conditional_edges(
        "decision_review",
        route_decision,
        {
            "FIGHT": "rebuttal_builder",
            "ACCEPT": "accept_and_log",
            "ESCALATE_DEGRADED": "human_escalation",
        },
    )
    graph.add_conditional_edges(
        "accept_and_log",
        route_learning,
        {"learn": "learning", "end": END},
    )
    graph.add_edge("rebuttal_builder", "quality_check")
    graph.add_conditional_edges(
        "quality_check",
        route_quality,
        {
            "approved": "filing",
            "retry": "rebuttal_builder",
            "escalate": "human_escalation",
        },
    )
    graph.add_conditional_edges(
        "filing",
        route_learning,
        {"learn": "learning", "end": END},
    )
    graph.add_conditional_edges(
        "human_escalation",
        route_learning,
        {"learn": "learning", "end": END},
    )
    graph.add_edge("learning", END)

    return graph


app = build_graph().compile()
