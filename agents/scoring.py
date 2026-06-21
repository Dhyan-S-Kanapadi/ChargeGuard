import logging
import os
from pathlib import Path

from core.state import ChargebackState
from ml.model import WinProbabilityModel


logger = logging.getLogger(__name__)


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return float(value)
    except ValueError:
        logger.warning("Invalid %s=%r, using default %s", name, value, default)
        return default


def _predict_win_probability(state: ChargebackState) -> tuple[float, str]:
    artifact_path = Path(
        os.getenv("MODEL_PATH", "./ml/artifacts/win_probability_model.pkl")
    )
    if not artifact_path.is_file():
        logger.error("Win probability model is unavailable at %s", artifact_path)
        return 0.0, "model_unavailable"

    try:
        model = WinProbabilityModel.load(artifact_path)
        probability = min(max(model.predict(state), 0.0), 1.0)
        return round(probability, 6), "logistic_regression"
    except Exception:
        logger.exception("Unable to load or execute win probability model")
        return 0.0, "model_error"


def scoring_agent(state: ChargebackState) -> ChargebackState:
    """Predict win probability and apply the deterministic EV decision rule."""
    logger.info("Running scoring agent for %s", state["chargeback_id"])

    win_probability, model_source = _predict_win_probability(state)
    response_cost = _float_env("RESPONSE_COST_USD", 15.0)
    fight_threshold = _float_env("FIGHT_EV_THRESHOLD", 0.0)
    expected_value = round((win_probability * state["dispute_amount"]) - response_cost, 2)
    decision = "FIGHT" if expected_value > fight_threshold else "ACCEPT"

    state["win_probability"] = win_probability
    state["expected_value"] = expected_value
    state["decision"] = decision
    state["decision_reasoning"] = (
        f"Model {model_source}; win probability {win_probability:.1%}; "
        f"expected value {expected_value:.2f} {state['currency']}; "
        f"threshold {fight_threshold:.2f}."
    )
    return state
