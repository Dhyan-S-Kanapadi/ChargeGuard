"""Run internal consistency checks against the current win-probability model."""

import argparse
import json
import os
from pathlib import Path

from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.model_selection import StratifiedKFold

from ml.model import WinProbabilityModel
from ml.synthetic_data import FeatureRow, generate_synthetic_dataset


DEFAULT_MODEL_PATH = "./ml/artifacts/win_probability_model.pkl"
DEFAULT_METADATA_PATH = "./ml/artifacts/training_metadata.json"


def _coefficient_map(model: WinProbabilityModel) -> dict[str, float]:
    classifier = model.pipeline.named_steps["classifier"]
    return {
        name: float(coefficient)
        for name, coefficient in zip(model.feature_names, classifier.coef_[0], strict=True)
    }


def _print_holdout_metrics(metadata_path: Path) -> None:
    if metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if {"holdout_accuracy", "holdout_roc_auc"}.issubset(metadata):
            print(
                "Latest saved holdout metrics: "
                f"accuracy={metadata['holdout_accuracy']:.4f}, "
                f"ROC-AUC={metadata['holdout_roc_auc']:.4f}"
            )
            return
    print(
        "Holdout accuracy and ROC-AUC are not persisted by ml.train.py. "
        "Run `poetry run python -m ml.train` to view its single-split metrics."
    )


def _cross_validate(count: int) -> None:
    rows, labels = generate_synthetic_dataset(count=count, seed=42)
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    accuracies: list[float] = []
    aucs: list[float] = []

    for train_indices, test_indices in folds.split(rows, labels):
        train_rows = [rows[index] for index in train_indices]
        test_rows = [rows[index] for index in test_indices]
        train_labels = [labels[index] for index in train_indices]
        test_labels = [labels[index] for index in test_indices]
        model = WinProbabilityModel(random_state=42).fit(train_rows, train_labels)
        probabilities = [model.predict_features(row) for row in test_rows]
        predictions = [int(probability >= 0.5) for probability in probabilities]
        accuracies.append(float(accuracy_score(test_labels, predictions)))
        aucs.append(float(roc_auc_score(test_labels, probabilities)))

    accuracy_mean = sum(accuracies) / len(accuracies)
    auc_mean = sum(aucs) / len(aucs)
    accuracy_std = (sum((value - accuracy_mean) ** 2 for value in accuracies) / len(accuracies)) ** 0.5
    auc_std = (sum((value - auc_mean) ** 2 for value in aucs) / len(aucs)) ** 0.5
    print(f"5-fold synthetic CV accuracy: {accuracy_mean:.4f} +/- {accuracy_std:.4f}")
    print(f"5-fold synthetic CV ROC-AUC: {auc_mean:.4f} +/- {auc_std:.4f}")


def _strong_features() -> FeatureRow:
    return {
        "otp_verified": 1,
        "three_ds_authenticated": 1,
        "customer_order_history": 20,
        "previous_chargebacks": 0,
        "delivery_confirmed": 1,
        "gps_coordinates_present": 1,
        "signature_obtained": 1,
        "fraud_score": 5.0,
        "vpn_detected": 0,
        "geolocation_match": 1,
        "consortium_lookup_complete": 1,
        "consortium_match": 0,
        "cross_merchant_fraud": 0,
        "post_delivery_contact": 1,
        "pre_chargeback_complaint": 0,
        "dispute_amount_bucket": 1,
        "card_network_encoded": 0,
        "reason_code_encoded": 0,
    }


def _run_feature_ablation(model: WinProbabilityModel) -> bool:
    baseline_features = _strong_features()
    baseline_probability = model.predict_features(baseline_features)
    coefficients = _coefficient_map(model)
    weak_values: dict[str, float | int] = {
        "otp_verified": 0,
        "three_ds_authenticated": 0,
        "delivery_confirmed": 0,
        "signature_obtained": 0,
        "fraud_score": 100.0,
        "vpn_detected": 1,
    }

    print(f"Representative strong-evidence win probability: {baseline_probability:.6f}")
    passed = True
    for feature_name, weak_value in weak_values.items():
        ablated = dict(baseline_features)
        ablated[feature_name] = weak_value
        probability = model.predict_features(ablated)
        delta = probability - baseline_probability
        expected_sign = coefficients[feature_name] * (
            float(weak_value) - float(baseline_features[feature_name])
        )
        direction_matches = delta * expected_sign > 0
        passed = passed and direction_matches
        status = "PASS" if direction_matches else "FAIL"
        expected_direction = "increase" if expected_sign > 0 else "decrease"
        print(
            f"{status} {feature_name}: probability delta={delta:+.6f}; "
            f"coefficient predicts {expected_direction}."
        )
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description="Check internal model consistency.")
    parser.add_argument(
        "--model-path",
        default=os.getenv("MODEL_PATH", DEFAULT_MODEL_PATH),
        help="Path to a WinProbabilityModel artifact.",
    )
    parser.add_argument(
        "--synthetic-count",
        type=int,
        default=200,
        help="Synthetic rows used for the 5-fold cross-validation check.",
    )
    args = parser.parse_args()
    if args.synthetic_count < 10:
        raise ValueError("--synthetic-count must be at least 10 for 5-fold cross-validation")

    model = WinProbabilityModel.load(args.model_path)
    print(f"Loaded model: {args.model_path} ({model.training_record_count} training rows)")
    _print_holdout_metrics(Path(os.getenv("TRAINING_METADATA_PATH", DEFAULT_METADATA_PATH)))
    _cross_validate(args.synthetic_count)
    ablation_passed = _run_feature_ablation(model)
    print(
        "This confirms the model behaves consistently with its own training signals. "
        "It does NOT confirm accuracy against real-world chargeback outcomes - that requires "
        "real filed-dispute results, not available yet."
    )
    return 0 if ablation_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
