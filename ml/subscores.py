import os
from pathlib import Path

from core.state import ChargebackState
from ml.features import features_from_state
from ml.model import WinProbabilityModel


_FRAUD_FEATURES = (
    ("fraud_score", 100.0),
    ("vpn_detected", 1.0),
    ("consortium_match", 1.0),
    ("cross_merchant_fraud", 1.0),
)
_IDENTITY_FEATURES = (
    ("otp_verified", 1.0),
    ("three_ds_authenticated", 1.0),
    ("delivery_confirmed", 1.0),
    ("signature_obtained", 1.0),
    ("customer_order_history", 50.0),
)


def _model_from_env() -> WinProbabilityModel:
    path = Path(os.getenv("MODEL_PATH", "./ml/artifacts/win_probability_model.pkl"))
    return WinProbabilityModel.load(path)


def _coefficient_map(model: WinProbabilityModel) -> dict[str, float]:
    classifier = model.pipeline.named_steps["classifier"]
    coefficients = classifier.coef_[0]
    return {
        feature_name: float(coefficient)
        for feature_name, coefficient in zip(model.feature_names, coefficients, strict=True)
    }


def _label(score: float) -> str:
    if score < 34:
        return "low"
    if score < 67:
        return "medium"
    return "high"


def _weighted_score(
    feature_values: dict[str, float | int],
    coefficients: dict[str, float],
    feature_bounds: tuple[tuple[str, float], ...],
    *,
    positive_direction: bool,
) -> dict[str, float | str]:
    weighted_signal = 0.0
    total_weight = 0.0

    for feature_name, maximum in feature_bounds:
        coefficient = coefficients[feature_name]
        weight = abs(coefficient)
        if weight == 0:
            continue

        normalized_value = min(max(float(feature_values[feature_name]) / maximum, 0.0), 1.0)
        aligns_with_model = coefficient > 0 if positive_direction else coefficient < 0
        weighted_signal += weight * (
            normalized_value if aligns_with_model else 1.0 - normalized_value
        )
        total_weight += weight

    score = 0.0 if total_weight == 0 else round((weighted_signal / total_weight) * 100, 1)
    return {"score": score, "label": _label(score)}


def subscores_from_state(state: ChargebackState) -> dict[str, dict[str, float | str]]:
    """Return model-coefficient-weighted evidence sub-scores on a 0-100 scale."""
    model = _model_from_env()
    features = features_from_state(state)
    coefficients = _coefficient_map(model)
    return {
        "third_party_fraud_indicators": _weighted_score(
            features,
            coefficients,
            _FRAUD_FEATURES,
            positive_direction=False,
        ),
        "identity_continuity": _weighted_score(
            features,
            coefficients,
            _IDENTITY_FEATURES,
            positive_direction=True,
        ),
    }
