from datetime import datetime, timedelta, timezone

import pytest

from agents.scoring import scoring_agent
from ml.train import train_baseline_model


@pytest.fixture(scope="module")
def scenario_model_path(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("scenario-model") / "model.pkl"
    train_baseline_model(output_path=path, count=200, seed=42)
    return str(path)


def _scenario_state(name: str, amount: float) -> dict:
    now = datetime(2026, 8, 27, tzinfo=timezone.utc)
    return {
        "chargeback_id": f"cb_scenario_{name}",
        "reason_code": "13.1",
        "card_network": "VISA",
        "dispute_amount": amount,
        "currency": "INR",
        "filing_deadline": now + timedelta(days=30),
        "chargeback_received_at": now,
        "merchant_profile": {
            "merchant_id": "merchant_scenarios",
            "name": "Scenario Merchant",
            "vertical": "ecommerce",
            "freshdesk_domain": "",
            "average_order_value": amount,
            "chargeback_history_count": 0,
        },
        "investigation_plan": {},
        "requires_food_agents": False,
        "transaction": {},
        "shipping": {},
        "device": {},
        "consortium": {"lookup_complete": True},
        "comms": {},
        "delivery_photo": None,
        "order_timeline": None,
        "evidence_collection_degraded": False,
        "degraded_reasons": [],
    }


def _genuine_non_delivery() -> dict:
    state = _scenario_state("genuine_non_delivery", 5_000.0)
    state["transaction"] = {
        "otp_verified": False,
        "three_ds_authenticated": False,
        "order_history_count": 1,
        "previous_chargebacks": 0,
    }
    state["shipping"] = {
        "status": "LOST",
        "status_category": "LOST",
        "signature_obtained": False,
    }
    state["device"] = {
        "fraud_score": 30.0,
        "vpn_detected": False,
        "geolocation_match": True,
    }
    state["comms"] = {"complaint_raised_before_chargeback": True}
    return state


def _friendly_fraud() -> dict:
    state = _scenario_state("friendly_fraud", 5_000.0)
    state["transaction"] = {
        "otp_verified": True,
        "three_ds_authenticated": True,
        "order_history_count": 10,
        "previous_chargebacks": 0,
    }
    state["shipping"] = {
        "status": "DELIVERED",
        "status_category": "CONFIRMED_DELIVERED",
        "delivery_latitude": 12.9716,
        "delivery_longitude": 77.5946,
        "signature_obtained": True,
    }
    state["device"] = {
        "fraud_score": 10.0,
        "vpn_detected": False,
        "geolocation_match": True,
    }
    state["comms"] = {
        "post_delivery_interaction": True,
        "complaint_raised_before_chargeback": False,
    }
    return state


def _stolen_card() -> dict:
    state = _scenario_state("stolen_card", 15_000.0)
    state["reason_code"] = "10.4"
    state["transaction"] = {
        "otp_verified": False,
        "three_ds_authenticated": False,
        "order_history_count": 0,
        "previous_chargebacks": 1,
    }
    state["shipping"] = {
        "status": "IN TRANSIT",
        "status_category": "IN_TRANSIT",
        "signature_obtained": False,
    }
    state["device"] = {
        "fraud_score": 95.0,
        "vpn_detected": True,
        "geolocation_match": False,
    }
    state["consortium"] = {
        "lookup_complete": True,
        "ethoca_match": True,
        "cross_merchant_fraud_history": True,
    }
    return state


@pytest.mark.parametrize(
    ("name", "state_factory", "expected_decision"),
    [
        ("genuine non-delivery", _genuine_non_delivery, "ACCEPT"),
        ("friendly fraud", _friendly_fraud, "FIGHT"),
        ("stolen card", _stolen_card, "ACCEPT"),
    ],
)
def test_india_decision_scenarios(
    monkeypatch,
    scenario_model_path: str,
    name: str,
    state_factory,
    expected_decision: str,
) -> None:
    monkeypatch.setenv("MODEL_PATH", scenario_model_path)
    monkeypatch.setenv("RESPONSE_COST_INR", "1200")
    state = state_factory()

    result = scoring_agent(state)
    probability = result["win_probability"]
    before_expected_value = round(probability * state["dispute_amount"] - 1245.0, 2)
    before_decision = "FIGHT" if before_expected_value > 0 else "ACCEPT"

    print(
        f"{name}: before={before_decision} EV={before_expected_value:.2f} INR; "
        f"after={result['decision']} EV={result['expected_value']:.2f} INR; "
        f"p={probability:.6f}"
    )
    assert result["decision"] == expected_decision
