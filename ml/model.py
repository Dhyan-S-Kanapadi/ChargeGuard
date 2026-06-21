import pickle
from pathlib import Path
from typing import Sequence

from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from core.state import ChargebackState
from ml.features import FEATURE_NAMES, features_from_state
from ml.synthetic_data import FeatureRow


class WinProbabilityModel:
    """Versioned sklearn baseline for chargeback win probability."""

    artifact_version = 2

    def __init__(self, *, random_state: int = 42) -> None:
        self.feature_names = FEATURE_NAMES
        self.training_record_count = 0
        self.pipeline = Pipeline(
            steps=[
                ("scaler", StandardScaler()),
                (
                    "classifier",
                    LogisticRegression(
                        max_iter=1_000,
                        random_state=random_state,
                    ),
                ),
            ]
        )

    def fit(self, rows: Sequence[FeatureRow], labels: Sequence[int]) -> "WinProbabilityModel":
        if len(rows) != len(labels):
            raise ValueError("rows and labels must have equal length")
        if len(rows) < 2 or len(set(labels)) != 2:
            raise ValueError("training data must contain both WIN and LOSS outcomes")

        matrix = [[float(row[name]) for name in self.feature_names] for row in rows]
        self.pipeline.fit(matrix, labels)
        self.training_record_count = len(rows)
        return self

    def predict_features(self, features: FeatureRow) -> float:
        matrix = [[float(features[name]) for name in self.feature_names]]
        probability = self.pipeline.predict_proba(matrix)[0][1]
        return float(probability)

    def predict(self, state: ChargebackState) -> float:
        return self.predict_features(features_from_state(state))

    def save(self, path: str | Path) -> Path:
        artifact_path = Path(path)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = artifact_path.with_suffix(f"{artifact_path.suffix}.tmp")
        with temporary_path.open("wb") as artifact:
            pickle.dump(self, artifact, protocol=pickle.HIGHEST_PROTOCOL)
        temporary_path.replace(artifact_path)
        return artifact_path

    @classmethod
    def load(cls, path: str | Path) -> "WinProbabilityModel":
        with Path(path).open("rb") as artifact:
            model = pickle.load(artifact)
        if not isinstance(model, cls):
            raise TypeError("artifact does not contain a WinProbabilityModel")
        if tuple(model.feature_names) != FEATURE_NAMES:
            raise ValueError("artifact feature schema does not match the current application")
        return model
