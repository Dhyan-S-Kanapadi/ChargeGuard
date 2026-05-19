import logging
from typing import Literal

from langgraph.graph import END, StateGraph

from core.state import ChargebackState


logger = logging.getLogger(__name__)


def _log_stub(name: str, state: ChargebackState) -> ChargebackState:
    logger.info("Running %s stub", name)
    return state


def orchestrator_agent(state: ChargebackState) -> ChargebackState:
    return _log_stub("orchestrator", state)


def collect_evidence_parallel(state: ChargebackState) -> ChargebackState:
    logger.info("Running collect_evidence stub")

    from agents.evidence.shipping import shipping_agent
    from agents.evidence.transaction import transaction_agent

    state = transaction_agent(state)
    return shipping_agent(state)


def collect_food_evidence(state: ChargebackState) -> ChargebackState:
    return _log_stub("collect_food_evidence", state)


def scoring_agent(state: ChargebackState) -> ChargebackState:
    return _log_stub("scoring", state)


def rebuttal_builder_agent(state: ChargebackState) -> ChargebackState:
    return _log_stub("rebuttal_builder", state)


def quality_check_agent(state: ChargebackState) -> ChargebackState:
    return _log_stub("quality_check", state)


def filing_agent(state: ChargebackState) -> ChargebackState:
    return _log_stub("filing", state)


def learning_agent(state: ChargebackState) -> ChargebackState:
    return _log_stub("learning", state)


def accept_and_log(state: ChargebackState) -> ChargebackState:
    return _log_stub("accept_and_log", state)


def human_escalation(state: ChargebackState) -> ChargebackState:
    return _log_stub("human_escalation", state)


def route_food_agents(state: ChargebackState) -> Literal["food", "standard"]:
    if state.get("requires_food_agents"):
        return "food"
    return "standard"


def route_decision(state: ChargebackState) -> Literal["FIGHT", "ACCEPT"]:
    if state.get("decision") == "FIGHT":
        return "FIGHT"
    return "ACCEPT"


def route_quality(state: ChargebackState) -> Literal["approved", "retry", "escalate"]:
    if state.get("quality_approved"):
        return "approved"
    if state.get("quality_loop_count", 0) >= 3:
        return "escalate"
    return "retry"


def build_graph():
    graph = StateGraph(ChargebackState)

    graph.add_node("orchestrator", orchestrator_agent)
    graph.add_node("collect_evidence", collect_evidence_parallel)
    graph.add_node("collect_food_evidence", collect_food_evidence)
    graph.add_node("scoring", scoring_agent)
    graph.add_node("rebuttal_builder", rebuttal_builder_agent)
    graph.add_node("quality_check", quality_check_agent)
    graph.add_node("filing", filing_agent)
    graph.add_node("learning", learning_agent)
    graph.add_node("accept_and_log", accept_and_log)
    graph.add_node("human_escalation", human_escalation)

    graph.set_entry_point("orchestrator")
    graph.add_edge("orchestrator", "collect_evidence")
    graph.add_conditional_edges(
        "collect_evidence",
        route_food_agents,
        {
            "food": "collect_food_evidence",
            "standard": "scoring",
        },
    )
    graph.add_edge("collect_food_evidence", "scoring")
    graph.add_conditional_edges(
        "scoring",
        route_decision,
        {
            "FIGHT": "rebuttal_builder",
            "ACCEPT": "accept_and_log",
        },
    )
    graph.add_edge("accept_and_log", "learning")
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
    graph.add_edge("filing", "learning")
    graph.add_edge("human_escalation", "learning")
    graph.add_edge("learning", END)

    return graph


app = build_graph().compile()
