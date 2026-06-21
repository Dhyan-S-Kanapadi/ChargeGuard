import argparse
import json
import os
from pathlib import Path
from typing import Any

from sklearn.metrics import accuracy_score, log_loss, roc_auc_score
from sklearn.model_selection import train_test_split

from ml.model import WinProbabilityModel
from ml.synthetic_data import FeatureRow, dataset_summary, generate_synthetic_dataset


DEFAULT_MODEL_PATH = "./ml/artifacts/win_probability_model.pkl"


def train_baseline_model(
    *,
    output_path: str | Path | None = None,
    count: int = 200,
    seed: int = 42,
) -> dict[str, Any]:
    rows, labels = generate_synthetic_dataset(count=count, seed=seed)
    train_rows, test_rows, train_labels, test_labels = train_test_split(
        rows,
        labels,
        test_size=0.25,
        random_state=seed,
        stratify=labels,
    )

    evaluation_model = WinProbabilityModel(random_state=seed).fit(train_rows, train_labels)
    probabilities = [evaluation_model.predict_features(row) for row in test_rows]
    predictions = [int(probability >= 0.5) for probability in probabilities]

    final_model = WinProbabilityModel(random_state=seed).fit(rows, labels)
    artifact_path = final_model.save(
        output_path or os.getenv("MODEL_PATH", DEFAULT_MODEL_PATH)
    )

    return {
        **dataset_summary(labels),
        "holdout_records": len(test_rows),
        "holdout_accuracy": accuracy_score(test_labels, predictions),
        "holdout_roc_auc": roc_auc_score(test_labels, probabilities),
        "holdout_log_loss": log_loss(test_labels, probabilities),
        "artifact_path": str(artifact_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the ChargeGuard baseline model")
    parser.add_argument("--output", default=None, help="Model artifact output path")
    parser.add_argument("--count", type=int, default=200, help="Synthetic record count")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic random seed")
    args = parser.parse_args()
    metrics = train_baseline_model(output_path=args.output, count=args.count, seed=args.seed)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
