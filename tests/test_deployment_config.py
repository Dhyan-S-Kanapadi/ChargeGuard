import logging
from pathlib import Path

from main import _log_deployment_warnings


ROOT = Path(__file__).resolve().parents[1]


def _env_example_values() -> dict[str, str]:
    values = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def test_docker_build_trains_model_and_uses_selected_port() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "RUN python -m ml.train" in dockerfile
    assert '${PORT:-8000}' in dockerfile
    assert "os.getenv('PORT', '8000')" in dockerfile
    assert dockerfile.index("COPY . ./") < dockerfile.index("RUN python -m ml.train")


def test_compose_provides_single_instance_persistent_data_volume() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "CHARGEGUARD_STORE_PATH" in compose
    assert "/var/data/chargeguard_store.json" in compose
    assert "chargeguard-data:/var/data" in compose
    assert "${PORT:-8000}:${PORT:-8000}" in compose


def test_env_example_has_safe_staging_placeholders() -> None:
    values = _env_example_values()
    assert values["PORT"] == "8000"
    assert values["ENVIRONMENT"] == "development"
    assert values["API_KEY"] == ""
    assert values["RAZORPAY_KEY_ID"] == ""
    assert values["RAZORPAY_KEY_SECRET"] == ""
    assert values["RAZORPAY_WEBHOOK_SECRET"] == ""
    assert values["RAZORPAY_WEBHOOK_ENABLED"] == "true"
    assert values["RAZORPAY_WEBHOOK_MAX_BODY_BYTES"] == "1048576"
    assert values["PROVIDER_EVENT_CLAIM_TIMEOUT_SECONDS"] == "300"
    assert values["RAZORPAY_SIMULATOR_ENABLED"] == "false"
    assert values["CHARGEGUARD_STORE_PATH"] == ""
    assert values["CHARGEGUARD_USE_STUBS"] == "true"
    assert values["MODEL_PATH"] == "./ml/artifacts/win_probability_model.pkl"


def test_production_startup_warns_about_missing_and_single_process_store(
    monkeypatch,
    caplog,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("CHARGEGUARD_STORE_PATH", raising=False)

    with caplog.at_level(logging.WARNING):
        _log_deployment_warnings()

    assert "will not survive a restart" in caplog.text
    assert "supports one application process only" in caplog.text
