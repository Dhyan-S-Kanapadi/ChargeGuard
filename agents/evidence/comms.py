import logging

from core.state import ChargebackState, CommsEvidence


logger = logging.getLogger(__name__)


def comms_agent(state: ChargebackState) -> ChargebackState:
    """Collect customer communication and support-ticket evidence."""
    logger.info("Running comms evidence agent for %s", state["chargeback_id"])

    customer_email = ""
    if state.get("transaction"):
        customer_email = state["transaction"]["customer_email"]

    evidence: CommsEvidence = {
        "emails": [
            {
                "from": customer_email or "customer@example.com",
                "subject": f"Order {state.get('order_id', state['chargeback_id'])} confirmation",
                "direction": "outbound",
            }
        ],
        "support_tickets": [],
        "post_delivery_interaction": True,
        "complaint_raised_before_chargeback": False,
        "raw": {
            "source": "comms_agent_stub",
            "freshdesk_domain": state["merchant_profile"].get("freshdesk_domain"),
        },
    }
    state["comms"] = evidence
    return state
