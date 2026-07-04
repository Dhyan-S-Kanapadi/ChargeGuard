from ml.model import WinProbabilityModel
from ml.synthetic_data import dataset_summary, generate_synthetic_dataset
from ml.train import train_baseline_model


def test_synthetic_dataset_is_deterministic_and_balanced() -> None:
    first_rows, first_labels = generate_synthetic_dataset(count=200, seed=42)
    second_rows, second_labels = generate_synthetic_dataset(count=200, seed=42)

    assert first_rows == second_rows
    assert first_labels == second_labels
    summary = dataset_summary(first_labels)
    assert summary["win_rate"] == 0.69
    assert summary["wins"] == 138


def test_model_round_trip_preserves_prediction(tmp_path) -> None:
    rows, labels = generate_synthetic_dataset(count=100, seed=7)
    model = WinProbabilityModel().fit(rows, labels)
    prediction = model.predict_features(rows[0])

    artifact_path = model.save(tmp_path / "model.pkl")
    loaded = WinProbabilityModel.load(artifact_path)

    assert loaded.training_record_count == 100
    assert loaded.predict_features(rows[0]) == prediction


def test_training_meets_baseline_accuracy_target(tmp_path) -> None:
    metrics = train_baseline_model(
        output_path=tmp_path / "baseline.pkl",
        count=200,
        seed=42,
    )

    assert metrics["holdout_accuracy"] >= 0.90
    assert metrics["holdout_roc_auc"] >= 0.90
    assert (tmp_path / "baseline.pkl").is_file()
