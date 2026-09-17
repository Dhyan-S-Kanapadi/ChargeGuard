from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from core.runtime import (
    RuntimeConfigurationError,
    assert_workflow_environment,
    provider_modes,
    validate_runtime_environment,
)


def production_config(tmp_path):
    return {
        "ENVIRONMENT": "production",
        "API_KEY": "a" * 40,
        "RAZORPAY_WEBHOOK_SECRET": "b" * 40,
        "CHARGEGUARD_USE_STUBS": "false",
        "CHARGEGUARD_LIVE_EVIDENCE_PROVIDERS": "razorpay,seon",
        "DATABASE_URL": "postgresql://placeholder.invalid/isolated-production",
        "CHARGEGUARD_CREDENTIAL_STORE_PATH": str(tmp_path / "secrets.json"),
        "CHARGEGUARD_CREDENTIAL_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    }


@pytest.mark.parametrize("environment", ["development", "test", "demo", "staging"])
def test_nonproduction_profiles_remain_available(environment):
    validate_runtime_environment({"ENVIRONMENT": environment, "API_KEY": "local-only",
                                  "CHARGEGUARD_USE_STUBS": "true",
                                  "RAZORPAY_WEBHOOK_ENABLED": "false"})


@pytest.mark.parametrize("environment", ["prodution", "", "Production-secret-value"])
def test_environment_typos_fail_without_echoing_values(environment):
    with pytest.raises(RuntimeConfigurationError) as error:
        validate_runtime_environment({"ENVIRONMENT": environment})
    assert "ENVIRONMENT" in str(error.value)
    if environment:
        assert environment not in str(error.value)


@pytest.mark.parametrize("name,value", [
    ("PUBLIC_DEMO_ENABLED", "true"), ("CHARGEGUARD_DEMO_SEED", "true"),
    ("RAZORPAY_SIMULATOR_ENABLED", "true"), ("CHARGEGUARD_USE_STUBS", "true"),
    ("SEON_USE_STUBS", "true"), ("GMAIL_USE_STUBS", "tru"),
    ("ALLOW_GLOBAL_PAYMENT_CREDENTIAL_FALLBACK", "true"),
    ("ALLOW_GLOBAL_SEON_CREDENTIAL_FALLBACK", "true"),
    ("API_KEY", ""), ("RAZORPAY_WEBHOOK_SECRET", ""),
    ("CHARGEGUARD_CREDENTIAL_ENCRYPTION_KEY", "not-a-fernet-key"),
    ("DATABASE_URL", ""), ("CHARGEGUARD_STORE_BACKEND", "local"),
    ("CHARGEGUARD_LIVE_EVIDENCE_PROVIDERS", "unknown-provider"),
])
def test_production_rejects_unsafe_configuration(tmp_path, name, value):
    config = production_config(tmp_path)
    config[name] = value
    with pytest.raises(RuntimeConfigurationError) as error:
        validate_runtime_environment(config)
    assert name in str(error.value)
    if config["RAZORPAY_WEBHOOK_SECRET"]:
        assert config["RAZORPAY_WEBHOOK_SECRET"] not in str(error.value)


def test_production_secrets_are_distinct(tmp_path):
    config = production_config(tmp_path)
    config["RAZORPAY_WEBHOOK_SECRET"] = config["API_KEY"]
    with pytest.raises(RuntimeConfigurationError, match="distinct"):
        validate_runtime_environment(config)


def test_provider_modes_do_not_claim_live_verification(tmp_path):
    config = production_config(tmp_path)
    validate_runtime_environment(config)
    modes = provider_modes(config)
    assert modes["razorpay"] == "live_enabled"
    assert modes["stripe"] == "unavailable"
    assert modes["food_platform"] == "unsupported"
    assert provider_modes({"CHARGEGUARD_USE_STUBS": "true"})["seon"] == "synthetic"


@pytest.mark.parametrize("state", [
    {"chargeback_id": "disp_SIM_123", "data_environment": "production"},
    {"chargeback_id": "disp_real", "data_environment": "staging"},
    {"chargeback_id": "disp_real"},
    {"chargeback_id": "disp_real", "data_environment": "production",
     "transaction": {"raw": {"source": "transaction_agent_stub"}}},
])
def test_production_rejects_cross_environment_or_synthetic_state(monkeypatch, state):
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(RuntimeConfigurationError, match="workflow"):
        assert_workflow_environment(state)


def test_invalid_startup_fails_before_demo_seed(monkeypatch):
    import main
    monkeypatch.setenv("ENVIRONMENT", "prodution")
    monkeypatch.setattr(main, "seed_demo_merchant", lambda: pytest.fail("seed called"))
    with pytest.raises(RuntimeConfigurationError, match="ENVIRONMENT"):
        with TestClient(main.app):
            pass


def test_disabled_live_provider_cannot_make_requests(monkeypatch):
    from integrations.razorpay import RazorpayClient
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("CHARGEGUARD_LIVE_EVIDENCE_PROVIDERS", "seon")
    with pytest.raises(RuntimeConfigurationError, match="razorpay"):
        RazorpayClient(key_id="not-secret", key_secret="not-secret",
                       client=httpx.Client(transport=httpx.MockTransport(
                           lambda request: pytest.fail("network called"))))


def test_live_input_stamp_and_development_compatibility(monkeypatch):
    from api.webhooks import build_initial_state
    monkeypatch.setenv("ENVIRONMENT", "staging")
    state = build_initial_state(
        chargeback_id="disp_test", order_id="order_test", payment_id="pay_test",
        reason_code="10.4", card_network="VISA", dispute_amount=1000,
        currency="INR", filing_deadline=datetime.now(timezone.utc) + timedelta(days=2),
        merchant_profile={"merchant_id": "merchant_test", "vertical": "ecommerce"},
    )
    assert state["data_environment"] == "staging"
    assert_workflow_environment(state)
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(RuntimeConfigurationError):
        assert_workflow_environment(state)


def test_production_never_creates_stub_filing_confirmation(monkeypatch, tmp_path):
    from agents.filing import filing_agent
    path = tmp_path / "packet.pdf"
    path.write_bytes(b"%PDF-1.4")
    monkeypatch.setenv("ENVIRONMENT", "production")
    state = {"chargeback_id": "disp_live", "data_environment": "production",
             "quality_approved": True, "rebuttal_document_path": str(path),
             "card_network": "VISA"}
    result = filing_agent(state)
    assert result["filed_at"] is None
    assert result["filing_confirmation"] == "filing_blocked_production_adapter_unavailable"


def test_runtime_status_requires_operator_key(monkeypatch):
    from main import app
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("API_KEY", "operator-key")
    client = TestClient(app)
    assert client.get("/internal/runtime").status_code == 401
    response = client.get("/internal/runtime", headers={"X-API-Key": "operator-key"})
    assert response.status_code == 200
    assert response.json()["production_ready"] is False
    assert "operator-key" not in response.text


@pytest.mark.parametrize("provider", ["razorpay", "stripe", "shiprocket", "delhivery",
                                     "seon", "gmail", "freshdesk", "ethoca", "verifi",
                                     "claude_vision", "food_platform"])
def test_production_never_uses_synthetic_evidence(monkeypatch, provider):
    from core.runtime import evidence_uses_stubs
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv(f"{provider.upper()}_USE_STUBS", "true")
    with pytest.raises(RuntimeConfigurationError, match="Synthetic"):
        evidence_uses_stubs(provider)


def test_blank_provider_override_inherits_global(monkeypatch):
    from core.runtime import evidence_uses_stubs
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "true")
    monkeypatch.setenv("SEON_USE_STUBS", "")
    assert evidence_uses_stubs("seon") is True


@pytest.mark.parametrize("environment", ["demo", "staging", None])
def test_production_cannot_load_nonproduction_store(monkeypatch, tmp_path, environment):
    import json
    from api.store import InMemoryStore
    path = tmp_path / "snapshot.json"
    payload = {"data_environment": environment} if environment else {}
    path.write_text(json.dumps(payload), encoding="utf-8")
    original = path.read_bytes()
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(RuntimeConfigurationError):
        InMemoryStore(path)
    assert path.read_bytes() == original


def test_store_retains_environment_across_restart(monkeypatch, tmp_path):
    import json
    from api.store import InMemoryStore
    monkeypatch.setenv("ENVIRONMENT", "staging")
    path = tmp_path / "snapshot.json"
    local = InMemoryStore(path)
    local.create_merchant({"merchant_id": "example", "name": "Example"})
    assert json.loads(path.read_text())["data_environment"] == "staging"
    assert InMemoryStore(path).get_merchant("example")["name"] == "Example"


def test_production_rejects_internal_simulation_before_persist(monkeypatch):
    from api.schemas import ChargebackWebhookPayload
    from api.webhooks import create_and_schedule_dispute
    from fastapi import BackgroundTasks, HTTPException
    from api import webhooks
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr(webhooks.store, "create_dispute", lambda state: pytest.fail("persisted"))
    payload = ChargebackWebhookPayload(
        chargeback_id="cb_123", merchant_id="merchant_test", order_id="order_test",
        payment_id="pay_test", reason_code="10.4", card_network="VISA",
        dispute_amount=1000, currency="INR", simulate_evidence_degraded=True,
        filing_deadline=datetime.now(timezone.utc) + timedelta(days=2),
    )
    with pytest.raises(HTTPException) as error:
        create_and_schedule_dispute(payload, {"merchant_id": "merchant_test"}, BackgroundTasks())
    assert error.value.status_code == 422
