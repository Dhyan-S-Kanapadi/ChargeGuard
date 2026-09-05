import json

import pytest

from ml.feedback import _retrain, record_outcome
from tests.test_learning_agent import _state


def _configure(monkeypatch, tmp_path, threshold: int = 10) -> None:
    monkeypatch.setenv("TRAINING_DATA_PATH", str(tmp_path / "outcomes.json"))
    monkeypatch.setenv("TRAINING_METADATA_PATH", str(tmp_path / "metadata.json"))
    monkeypatch.setenv("PLAYBOOK_STATS_PATH", str(tmp_path / "stats.json"))
    monkeypatch.setenv("MODEL_PATH", str(tmp_path / "model.pkl"))
    monkeypatch.setenv("RETRAIN_RECORD_THRESHOLD", str(threshold))


def _record(index: int) -> dict:
    state = _state()
    state["chargeback_id"] = f"cb_retrain_{index:03d}"
    state["final_outcome"] = "WIN" if index % 2 == 0 else "LOSS"
    return {
        "chargeback_id": state["chargeback_id"],
        "card_network": state["card_network"],
        "reason_code": state["reason_code"],
        "outcome": state["final_outcome"],
        "label": int(state["final_outcome"] == "WIN"),
        "features": {
            "otp_verified": int(index % 2 == 0),
            "three_ds_authenticated": int(index % 3 == 0),
            "customer_order_history": index % 50,
            "previous_chargebacks": index % 4,
            "delivery_confirmed": int(index % 2 == 0),
            "gps_coordinates_present": int(index % 3 == 0),
            "signature_obtained": int(index % 4 == 0),
            "fraud_score": float(index % 100),
            "vpn_detected": int(index % 5 == 0),
            "geolocation_match": int(index % 2 == 0),
            "consortium_lookup_complete": 1,
            "consortium_match": int(index % 7 == 0),
            "cross_merchant_fraud": int(index % 11 == 0),
            "post_delivery_contact": int(index % 3 == 0),
            "pre_chargeback_complaint": int(index % 5 == 0),
            "dispute_amount_bucket": index % 4,
            "card_network_encoded": 0,
            "reason_code_encoded": 1,
        },
        "recorded_at": "2026-05-12T00:00:00+00:00",
    }


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


@pytest.mark.parametrize("outcome", ["WIN", "LOSS"])
def test_simulator_outcomes_never_write_training_data(monkeypatch, tmp_path, outcome) -> None:
    from agents.learning import learning_agent

    _configure(monkeypatch, tmp_path)
    state = _state()
    state["chargeback_id"] = "disp_SIM_learning_test"
    state["final_outcome"] = outcome
    learning_agent(state)
    with pytest.raises(ValueError, match="synthetic simulator"):
        record_outcome(state)
    assert not (tmp_path / "outcomes.json").exists()
    assert not (tmp_path / "model.pkl").exists()


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
    assert metadata["training_split"] == {
        "real_record_count": 10,
        "synthetic_decay_per_real_record": 4,
        "synthetic_record_count": 160,
        "synthetic_seed_count": 200,
        "training_row_count": 170,
    }
    assert result["training_split"] == metadata["training_split"]
    assert statistics["VISA"]["13.1"] == {
        "losses": 3,
        "total": 10,
        "win_rate": 0.7,
        "wins": 7,
    }


def test_feedback_rejects_no_contest_acceptance(monkeypatch, tmp_path) -> None:
    _configure(monkeypatch, tmp_path)
    state = _state()
    state["decision"] = "ACCEPT"
    state["filing_confirmation"] = "accepted_no_filing"
    state["filed_at"] = None
    state["final_outcome"] = "ACCEPTED_NO_CONTEST"

    with pytest.raises(ValueError, match="terminal WIN or LOSS"):
        record_outcome(state)

    assert not (tmp_path / "outcomes.json").exists()


def test_retrain_uses_full_synthetic_seed_without_real_records(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("MODEL_PATH", str(tmp_path / "model-empty.pkl"))

    artifact_path, metadata = _retrain([])

    assert artifact_path == tmp_path / "model-empty.pkl"
    assert metadata["real_record_count"] == 0
    assert metadata["synthetic_record_count"] == 200
    assert metadata["training_row_count"] == 200


def test_retrain_decays_synthetic_records_with_real_records(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("MODEL_PATH", str(tmp_path / "model-mid.pkl"))

    _, metadata = _retrain([_record(index) for index in range(25)])

    assert metadata["real_record_count"] == 25
    assert metadata["synthetic_record_count"] == 100
    assert metadata["training_row_count"] == 125


def test_retrain_drops_synthetic_records_after_fifty_real_records(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("MODEL_PATH", str(tmp_path / "model-real.pkl"))

    _, metadata = _retrain([_record(index) for index in range(50)])

    assert metadata["real_record_count"] == 50
    assert metadata["synthetic_record_count"] == 0
    assert metadata["training_row_count"] == 50


def test_feedback_rejects_unfiled_loss(monkeypatch, tmp_path) -> None:
    _configure(monkeypatch, tmp_path)
    state = _state()
    state["filing_confirmation"] = "accepted_no_filing"
    state["filed_at"] = None
    state["final_outcome"] = "LOSS"

    with pytest.raises(ValueError, match="filed representment"):
        record_outcome(state)

    assert not (tmp_path / "outcomes.json").exists()
