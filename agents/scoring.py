import logging

from core.state import ChargebackState


logger = logging.getLogger(__name__)


def _add(signal_score: float, condition: bool, weight: float) -> float:
    return signal_score + weight if condition else signal_score


def scoring_agent(state: ChargebackState) -> ChargebackState:
    """Estimate win probability and decide whether to fight."""
    logger.info("Running scoring agent for %s", state["chargeback_id"])

    score = 0.18
    transaction = state.get("transaction")
    shipping = state.get("shipping")
    device = state.get("device")
    comms = state.get("comms")
    consortium = state.get("consortium")
    delivery_photo = state.get("delivery_photo")
    timeline = state.get("order_timeline")

    if transaction:
        score = _add(score, transaction["otp_verified"], 0.16)
        score = _add(score, transaction["three_ds_authenticated"], 0.16)
        score = _add(score, transaction["previous_chargebacks"] == 0, 0.08)
        score = _add(score, transaction["order_history_count"] >= 3, 0.06)

    if shipping:
        score = _add(score, shipping["status"].upper() == "DELIVERED", 0.14)
        score = _add(score, shipping["signature_obtained"], 0.08)
        score = _add(score, bool(shipping["delivery_photo_url"]), 0.05)

    if device:
        score = _add(score, device["geolocation_match"], 0.06)
        score = _add(score, device["login_pattern_normal"], 0.05)
        score = _add(score, not device["vpn_detected"], 0.04)
        score = _add(score, device["fraud_score"] < 40, 0.05)

    if comms:
        score = _add(score, comms["post_delivery_interaction"], 0.05)
        score = _add(score, not comms["complaint_raised_before_chargeback"], 0.05)

    if consortium:
        score = _add(score, not consortium["cross_merchant_fraud_history"], 0.04)
        score = _add(score, consortium["dispute_count_across_merchants"] <= 1, 0.04)

    if delivery_photo:
        score = _add(score, delivery_photo["ai_verified"], 0.06)
        score = _add(score, delivery_photo["address_visible"], 0.04)

    if timeline:
        score = _add(score, timeline["delivered_at"] is not None, 0.05)
        score = _add(score, (timeline["post_delivery_rating"] or 0) >= 4, 0.03)

    win_probability = max(0.01, min(round(score, 4), 0.95))
    expected_value = round((win_probability * state["dispute_amount"]) - ((1 - win_probability) * 0.15 * state["dispute_amount"]), 2)

    state["win_probability"] = win_probability
    state["expected_value"] = expected_value
    state["decision"] = "FIGHT" if expected_value > 0 and win_probability >= 0.5 else "ACCEPT"
    state["decision_reasoning"] = (
        f"Win probability {win_probability:.0%}; expected value {expected_value:.2f} {state['currency']}."
    )
    return state
