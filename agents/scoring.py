import logging
import os
from pathlib import Path

from agents.contradiction import contradictions_from_state
from core.config import response_cost_for_currency
from core.state import ChargebackState
from ml.model import WinProbabilityModel
from ml.subscores import subscores_from_state


logger = logging.getLogger(__name__)


def _decision_log_extra(state: ChargebackState) -> dict[str, object]:
    return {
        "chargeback_id": state["chargeback_id"],
        "decision": state.get("decision"),
        "win_probability": state.get("win_probability"),
        "expected_value": state.get("expected_value"),
        "dispute_amount": state["dispute_amount"],
        "currency": state["currency"],
    }


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
    win_probability, model_source = _predict_win_probability(state)
    subscores: dict[str, dict[str, float | str]] | None = None
    if model_source == "logistic_regression":
        try:
            subscores = subscores_from_state(state)
        except Exception:
            logger.exception("Unable to calculate model-backed evidence sub-scores")

    contradictions = contradictions_from_state(state)
    response_cost = response_cost_for_currency(state["currency"])
    fight_threshold = _float_env("FIGHT_EV_THRESHOLD", 0.0)
    expected_value = round((win_probability * state["dispute_amount"]) - response_cost, 2)
    is_degraded = state.get("evidence_collection_degraded", False)
    model_failed = model_source != "logistic_regression"
    if is_degraded or model_failed:
        decision = "ESCALATE_DEGRADED"
    else:
        decision = "FIGHT" if expected_value > fight_threshold else "ACCEPT"

    state["win_probability"] = win_probability
    state["expected_value"] = expected_value
    state["third_party_fraud_indicators"] = (
        subscores["third_party_fraud_indicators"] if subscores else None
    )
    state["identity_continuity"] = subscores["identity_continuity"] if subscores else None
    state["contradiction_flags"] = contradictions["flags"]
    state["contradiction_summary"] = contradictions["summary"]
    state["decision"] = decision
    degradation_reason = " Evidence or model availability is degraded; human review required." if (
        is_degraded or model_failed
    ) else ""
    expedited_reason = " Expedited partial-evidence decision due to overdue filing deadline." if (
        state.get("investigation_plan", {}).get("priority") == "overdue"
    ) else ""
    subscore_reason = ""
    if subscores:
        fraud = subscores["third_party_fraud_indicators"]
        identity = subscores["identity_continuity"]
        subscore_reason = (
            " Third-party fraud indicators "
            f"{fraud['score']:.1f}/100 ({fraud['label']}); identity continuity "
            f"{identity['score']:.1f}/100 ({identity['label']})."
        )
    contradiction_reason = (
        f" {contradictions['summary']}" if contradictions["summary"] else ""
    )
    state["decision_reasoning"] = (
        f"Model {model_source}; win probability {win_probability:.1%}; "
        f"expected value {expected_value:.2f} {state['currency']}; "
        f"threshold {fight_threshold:.2f}."
        + subscore_reason
        + contradiction_reason
        + degradation_reason
        + expedited_reason
    )
    logger.info(
        "Running scoring agent for %s",
        state["chargeback_id"],
        extra=_decision_log_extra(state),
    )
    return state
