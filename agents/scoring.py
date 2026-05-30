import logging
import os
from dataclasses import dataclass

from core.state import ChargebackState


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScoreSignal:
    name: str
    matched: bool
    weight: float


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return float(value)
    except ValueError:
        logger.warning("Invalid %s=%r, using default %s", name, value, default)
        return default


def _add(signal_score: float, condition: bool, weight: float) -> float:
    return signal_score + weight if condition else signal_score


def _score_signals(state: ChargebackState) -> list[ScoreSignal]:
    signals: list[ScoreSignal] = []
    transaction = state.get("transaction")
    shipping = state.get("shipping")
    device = state.get("device")
    comms = state.get("comms")
    consortium = state.get("consortium")
    delivery_photo = state.get("delivery_photo")
    timeline = state.get("order_timeline")

    if transaction:
        signals.extend(
            [
                ScoreSignal("otp_verified", transaction["otp_verified"], 0.16),
                ScoreSignal("three_ds_authenticated", transaction["three_ds_authenticated"], 0.16),
                ScoreSignal("no_previous_chargebacks", transaction["previous_chargebacks"] == 0, 0.08),
                ScoreSignal("repeat_customer", transaction["order_history_count"] >= 3, 0.06),
            ]
        )

    if shipping:
        signals.extend(
            [
                ScoreSignal("delivered", shipping["status"].upper() == "DELIVERED", 0.14),
                ScoreSignal("signature_obtained", shipping["signature_obtained"], 0.08),
                ScoreSignal("delivery_photo_available", bool(shipping["delivery_photo_url"]), 0.05),
            ]
        )

    if device:
        signals.extend(
            [
                ScoreSignal("device_geolocation_match", device["geolocation_match"], 0.06),
                ScoreSignal("normal_login_pattern", device["login_pattern_normal"], 0.05),
                ScoreSignal("no_vpn_detected", not device["vpn_detected"], 0.04),
                ScoreSignal("low_device_fraud_score", device["fraud_score"] < 40, 0.05),
            ]
        )

    if comms:
        signals.extend(
            [
                ScoreSignal("post_delivery_interaction", comms["post_delivery_interaction"], 0.05),
                ScoreSignal("no_prior_complaint", not comms["complaint_raised_before_chargeback"], 0.05),
            ]
        )

    if consortium:
        signals.extend(
            [
                ScoreSignal("no_cross_merchant_fraud", not consortium["cross_merchant_fraud_history"], 0.04),
                ScoreSignal("low_consortium_dispute_count", consortium["dispute_count_across_merchants"] <= 1, 0.04),
            ]
        )

    if delivery_photo:
        signals.extend(
            [
                ScoreSignal("delivery_photo_ai_verified", delivery_photo["ai_verified"], 0.06),
                ScoreSignal("delivery_photo_address_visible", delivery_photo["address_visible"], 0.04),
            ]
        )

    if timeline:
        signals.extend(
            [
                ScoreSignal("timeline_delivered", timeline["delivered_at"] is not None, 0.05),
                ScoreSignal("positive_post_delivery_rating", (timeline["post_delivery_rating"] or 0) >= 4, 0.03),
            ]
        )

    return signals


def scoring_agent(state: ChargebackState) -> ChargebackState:
    """Estimate win probability and decide whether to fight."""
    logger.info("Running scoring agent for %s", state["chargeback_id"])

    score = 0.18
    signals = _score_signals(state)
    for signal in signals:
        score = _add(score, signal.matched, signal.weight)

    win_probability = max(0.01, min(round(score, 4), 0.95))
    response_cost = _float_env("RESPONSE_COST_USD", 15.0)
    fight_threshold = _float_env("FIGHT_EV_THRESHOLD", 0.0)
    expected_value = round((win_probability * state["dispute_amount"]) - response_cost, 2)
    matched_signals = [signal.name for signal in signals if signal.matched]

    state["win_probability"] = win_probability
    state["expected_value"] = expected_value
    state["decision"] = "FIGHT" if expected_value > fight_threshold and win_probability >= 0.5 else "ACCEPT"
    state["decision_reasoning"] = (
        f"Win probability {win_probability:.0%}; expected value {expected_value:.2f} {state['currency']}; "
        f"matched signals: {', '.join(matched_signals) if matched_signals else 'none'}."
    )
    return state
