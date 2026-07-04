import json

from ml.feedback import record_outcome
from tests.test_learning_agent import _state


def _configure(monkeypatch, tmp_path, threshold: int = 10) -> None:
    monkeypatch.setenv("TRAINING_DATA_PATH", str(tmp_path / "outcomes.json"))
    monkeypatch.setenv("TRAINING_METADATA_PATH", str(tmp_path / "metadata.json"))
    monkeypatch.setenv("PLAYBOOK_STATS_PATH", str(tmp_path / "stats.json"))
    monkeypatch.setenv("MODEL_PATH", str(tmp_path / "model.pkl"))
    monkeypatch.setenv("RETRAIN_RECORD_THRESHOLD", str(threshold))


def test_feedback_is_idempotent_by_chargeback_id(monkeypatch, tmp_path) -> None:
    _configure(monkeypatch, tmp_path)
    state = _state()
    state["final_outcome"] = "WIN"

    first = record_outcome(state)
    state["final_outcome"] = "LOSS"
    second = record_outcome(state)

    records = json.loads((tmp_path / "outcomes.json").read_text(encoding="utf-8"))
    assert first["created"] is True
    assert second["created"] is False
    assert second["record_count"] == 1
    assert records[0]["outcome"] == "LOSS"


def test_feedback_retrains_after_threshold(monkeypatch, tmp_path) -> None:
    _configure(monkeypatch, tmp_path, threshold=10)

    result = None
    for index in range(10):
        state = _state()
        state["chargeback_id"] = f"cb_feedback_{index:03d}"
        state["final_outcome"] = "WIN" if index < 7 else "LOSS"
        result = record_outcome(state)

    assert result is not None
    assert result["record_count"] == 10
    assert result["retrained"] is True
    assert (tmp_path / "model.pkl").is_file()

    metadata = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
    statistics = json.loads((tmp_path / "stats.json").read_text(encoding="utf-8"))
    assert metadata["last_trained_record_count"] == 10
    assert statistics["VISA"]["13.1"] == {
        "losses": 3,
        "total": 10,
        "win_rate": 0.7,
        "wins": 7,
    }
