import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from core.state import ChargebackState, is_filed_dispute
from ml.features import FEATURE_NAMES, features_from_state
from ml.model import WinProbabilityModel
from ml.synthetic_data import generate_synthetic_dataset


_LOCK = RLock()
logger = logging.getLogger(__name__)


def _path(env_name: str, default: str) -> Path:
    return Path(os.getenv(env_name, default))


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid %s=%r, using default %s", name, value, default)
        return default


def _feedback_record(state: ChargebackState) -> dict[str, Any]:
    outcome = state.get("final_outcome")
    if outcome not in {"WIN", "LOSS"}:
        raise ValueError("feedback requires a terminal WIN or LOSS outcome")
    if not is_filed_dispute(state):
        raise ValueError("feedback requires a filed representment case")
    return {
        "chargeback_id": state["chargeback_id"],
        "card_network": state["card_network"],
        "reason_code": state["reason_code"],
        "outcome": outcome,
        "label": int(outcome == "WIN"),
        "features": features_from_state(state),
        "recorded_at": (
            state.get("outcome_recorded_at") or datetime.now(timezone.utc)
        ).isoformat(),
    }


def _playbook_statistics(records: list[dict[str, Any]]) -> dict[str, Any]:
    statistics: dict[str, Any] = {}
    for record in records:
        network = statistics.setdefault(record["card_network"], {})
        reason = network.setdefault(
            record["reason_code"],
            {"wins": 0, "losses": 0, "total": 0, "win_rate": 0.0},
        )
        reason["wins" if record["outcome"] == "WIN" else "losses"] += 1
        reason["total"] += 1
        reason["win_rate"] = reason["wins"] / reason["total"]
    return statistics


def _retrain(records: list[dict[str, Any]]) -> Path:
    rows, labels = generate_synthetic_dataset(count=200, seed=42)
    for record in records:
        features = record["features"]
        rows.append({name: features[name] for name in FEATURE_NAMES})
        labels.append(int(record["label"]))
    model = WinProbabilityModel(random_state=42).fit(rows, labels)
    return model.save(os.getenv("MODEL_PATH", "./ml/artifacts/win_probability_model.pkl"))


def record_outcome(state: ChargebackState) -> dict[str, Any]:
    """Persist one terminal result and retrain after each ten new real cases."""
    dataset_path = _path("TRAINING_DATA_PATH", "./ml/artifacts/outcomes.json")
    metadata_path = _path(
        "TRAINING_METADATA_PATH", "./ml/artifacts/training_metadata.json"
    )
    statistics_path = _path(
        "PLAYBOOK_STATS_PATH", "./ml/artifacts/playbook_stats.json"
    )
    threshold = int(os.getenv("RETRAIN_RECORD_THRESHOLD", "10"))
    if threshold < 1:
        raise ValueError("RETRAIN_RECORD_THRESHOLD must be positive")

    record = _feedback_record(state)
    with _LOCK:
        records = _read_json(dataset_path, [])
        existing_index = next(
            (
                index
                for index, existing in enumerate(records)
                if existing["chargeback_id"] == record["chargeback_id"]
            ),
            None,
        )
        created = existing_index is None
        if created:
            records.append(record)
        else:
            records[existing_index] = record
        _write_json(dataset_path, records)
        _write_json(statistics_path, _playbook_statistics(records))

        metadata = _read_json(metadata_path, {"last_trained_record_count": 0})
        last_trained = int(metadata.get("last_trained_record_count", 0))
        retrained = len(records) - last_trained >= threshold
        artifact_path: str | None = None
        if retrained:
            artifact_path = str(_retrain(records))
            metadata = {
                "last_trained_record_count": len(records),
                "trained_at": datetime.now(timezone.utc).isoformat(),
                "model_path": artifact_path,
            }
            _write_json(metadata_path, metadata)

    return {
        "created": created,
        "record_count": len(records),
        "retrained": retrained,
        "model_path": artifact_path,
    }
