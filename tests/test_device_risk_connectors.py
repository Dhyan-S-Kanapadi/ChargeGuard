from datetime import datetime, timedelta, timezone
import json

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import httpx
import pytest

from agents.evidence import device
from api.store import InMemoryStore, store
from api.webhooks import build_initial_state
from integrations.credential_secrets import (
    CredentialStoreError,
    credential_secret_store_from_env,
    reset_credential_secret_store_cache,
)
from integrations.device_risk_client_factory import (
    DeviceRiskClientFactory,
    DeviceRiskConnectorError,
)
from integrations.seon import SeonRequestError
from main import app


@pytest.fixture(autouse=True)
def connector_environment(tmp_path, monkeypatch):
    store.clear()
    reset_credential_secret_store_cache()
    monkeypatch.setenv("API_KEY", "test-api-key")
    monkeypatch.setenv("CHARGEGUARD_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("CHARGEGUARD_CREDENTIAL_STORE_PATH", str(tmp_path / "provider-secrets.json"))
    monkeypatch.delenv("ALLOW_GLOBAL_SEON_CREDENTIAL_FALLBACK", raising=False)
    monkeypatch.delenv("SEON_API_KEY", raising=False)
    yield
    store.clear()
    reset_credential_secret_store_cache()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, headers={"X-API-Key": "test-api-key"})


def _merchant(merchant_id: str) -> dict:
    return {
        "merchant_id": merchant_id,
        "name": merchant_id,
        "vertical": "ecommerce",
        "freshdesk_domain": "",
        "average_order_value": 1000.0,
        "chargeback_history_count": 0,
    }


def _create_merchant(client: TestClient, merchant_id: str) -> None:
    assert client.post("/merchants", json=_merchant(merchant_id)).status_code == 201


def _connect(client: TestClient, merchant_id: str, api_key: str):
    return client.post(
        f"/merchants/{merchant_id}/device-risk-connectors/seon",
        json={"api_key": api_key},
    )


def _state(merchant_id: str):
    state = build_initial_state(
        chargeback_id=f"cb-{merchant_id}",
        order_id="order-1",
        payment_id="pay-1",
        reason_code="10.4",
        card_network="VISA",
        dispute_amount=100.0,
        currency="INR",
        filing_deadline=datetime.now(timezone.utc) + timedelta(days=5),
        merchant_profile=store.get_merchant(merchant_id),
    )
    state["transaction"] = {
        "order_id": "order-1",
        "payment_id": "pay-1",
        "amount": 100.0,
        "currency": "INR",
        "otp_verified": True,
        "three_ds_authenticated": True,
        "device_id": "device-1",
        "ip_address": "203.0.113.10",
        "customer_email": "customer@example.test",
        "order_history_count": 2,
        "previous_chargebacks": 0,
        "raw": {},
    }
    return state


class SuccessfulSeon:
    def __init__(self, api_key: str, observed: list[str] | None = None):
        if observed is not None:
            observed.append(api_key)

    def fraud_check(self, payload: dict) -> dict:
        return {
            "success": True,
            "data": {
                "fraud_score": 14,
                "device_details": {"device_hash": "normalized-device"},
                "ip_details": {"is_vpn": False},
            },
        }


def test_merchants_resolve_distinct_seon_credentials(client) -> None:
    _create_merchant(client, "merchant-a")
    _create_merchant(client, "merchant-b")
    _connect(client, "merchant-a", "seon-merchant-a-key")
    _connect(client, "merchant-b", "seon-merchant-b-key")
    observed: list[str] = []
    factory = DeviceRiskClientFactory(
        store,
        seon_builder=lambda **values: SuccessfulSeon(values["api_key"], observed),
    )

    factory.for_merchant(store.get_merchant("merchant-a"))
    factory.for_merchant(store.get_merchant("merchant-b"))

    assert observed == ["seon-merchant-a-key", "seon-merchant-b-key"]


def test_cross_merchant_access_is_rejected(client) -> None:
    _create_merchant(client, "merchant-a")
    _create_merchant(client, "merchant-b")
    connector_id = _connect(client, "merchant-b", "seon-merchant-b-key").json()["connector_id"]

    assert client.post(
        f"/merchants/merchant-a/device-risk-connectors/{connector_id}/verify"
    ).status_code == 404
    assert client.delete(
        f"/merchants/merchant-a/device-risk-connectors/{connector_id}"
    ).status_code == 404
    assert client.get("/merchants/merchant-a/device-risk-connectors").json() == []
    profile = store.get_merchant("merchant-a")
    profile["device_risk_connector_id"] = connector_id
    with pytest.raises(DeviceRiskConnectorError, match="device_risk_connector_not_found"):
        DeviceRiskClientFactory(store).for_merchant(profile)


def test_first_real_request_verifies_connector_and_keeps_only_normalized_evidence(
    client, monkeypatch
) -> None:
    _create_merchant(client, "merchant-a")
    connector_id = _connect(client, "merchant-a", "seon-merchant-a-key").json()["connector_id"]
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "false")
    monkeypatch.setattr(
        device,
        "device_risk_client_factory",
        DeviceRiskClientFactory(store, seon_builder=lambda **_: SuccessfulSeon("unused")),
    )

    result = device.device_agent(_state("merchant-a"))

    connector = store.get_device_risk_connector("merchant-a", connector_id)
    assert connector["status"] == "verified"
    assert connector["verified_at"] is not None
    assert connector["last_success_at"] is not None
    assert result["device"]["raw"] == {"source": "seon"}
    serialized = json.dumps(result, default=str)
    assert "api_key" not in serialized
    assert "seon-merchant-a-key" not in serialized


def test_successful_rotation_switches_connector_and_removes_old_secret(
    client, monkeypatch
) -> None:
    _create_merchant(client, "merchant-a")
    original_id = _connect(client, "merchant-a", "seon-original-key").json()["connector_id"]
    store.activate_device_risk_connector("merchant-a", original_id)
    replacement_id = _connect(client, "merchant-a", "seon-replacement-key").json()["connector_id"]
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "false")
    monkeypatch.setattr(
        device,
        "device_risk_client_factory",
        DeviceRiskClientFactory(store, seon_builder=lambda **_: SuccessfulSeon("unused")),
    )

    device.device_agent(_state("merchant-a"))

    assert store.get_merchant("merchant-a")["device_risk_connector_id"] == replacement_id
    assert store.get_device_risk_connector("merchant-a", replacement_id)["status"] == "verified"
    assert store.get_device_risk_connector("merchant-a", original_id)["status"] == "disconnected"
    with pytest.raises(CredentialStoreError, match="credential_not_found"):
        credential_secret_store_from_env().get(original_id)
    assert "rotated" in {
        item["action"] for item in store.list_device_risk_connector_audit("merchant-a")
    }


@pytest.mark.parametrize("status_code", [401, 403])
def test_authentication_failure_invalidates_candidate_and_preserves_rotation(
    client, monkeypatch, status_code
) -> None:
    _create_merchant(client, "merchant-a")
    original_id = _connect(client, "merchant-a", "seon-original-key").json()["connector_id"]
    store.activate_device_risk_connector("merchant-a", original_id)
    replacement_id = _connect(client, "merchant-a", "seon-replacement-key").json()["connector_id"]
    assert store.get_merchant("merchant-a")["device_risk_connector_id"] == original_id

    class RejectedSeon:
        def __init__(self, **_):
            pass

        def fraud_check(self, payload):
            raise SeonRequestError("seon_request_failed", status_code=status_code)

    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "false")
    monkeypatch.setattr(
        device,
        "device_risk_client_factory",
        DeviceRiskClientFactory(store, seon_builder=RejectedSeon),
    )

    result = device.device_agent(_state("merchant-a"))

    assert result["device"] is None
    assert "device_provider_unavailable" in result["degraded_reasons"]
    assert store.get_device_risk_connector("merchant-a", replacement_id)["status"] == "invalid"
    assert store.get_merchant("merchant-a")["device_risk_connector_id"] == original_id
    assert store.get_device_risk_connector("merchant-a", original_id)["status"] == "verified"
    with pytest.raises(CredentialStoreError, match="credential_not_found"):
        credential_secret_store_from_env().get(replacement_id)


@pytest.mark.parametrize("failure", ["timeout", "server"])
def test_transient_failures_do_not_invalidate_connector(client, monkeypatch, failure) -> None:
    _create_merchant(client, "merchant-a")
    connector_id = _connect(client, "merchant-a", "seon-transient-key").json()["connector_id"]

    class UnavailableSeon:
        def __init__(self, **_):
            pass

        def fraud_check(self, payload):
            if failure == "timeout":
                raise httpx.ReadTimeout("timeout")
            raise SeonRequestError("seon_request_failed", status_code=503)

    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "false")
    monkeypatch.setattr(
        device,
        "device_risk_client_factory",
        DeviceRiskClientFactory(store, seon_builder=UnavailableSeon),
    )

    result = device.device_agent(_state("merchant-a"))

    assert result["device"] is None
    assert store.get_device_risk_connector("merchant-a", connector_id)["status"] == "verification_pending"
    assert credential_secret_store_from_env().get(connector_id) == {"api_key": "seon-transient-key"}


def test_global_fallback_is_explicit_and_never_masks_invalid_connector(client, monkeypatch) -> None:
    _create_merchant(client, "merchant-a")
    profile = store.get_merchant("merchant-a")
    monkeypatch.setenv("SEON_API_KEY", "global-seon-key")
    with pytest.raises(DeviceRiskConnectorError, match="not_configured"):
        DeviceRiskClientFactory(store).for_merchant(profile)

    monkeypatch.setenv("ALLOW_GLOBAL_SEON_CREDENTIAL_FALLBACK", "true")
    assert DeviceRiskClientFactory(store).for_merchant(profile).api_key == "global-seon-key"
    connector_id = _connect(client, "merchant-a", "invalid-seon-key").json()["connector_id"]
    store.update_device_risk_connector_status(
        "merchant-a",
        connector_id,
        status="invalid",
        last_error_code="provider_authentication_failed",
        audit_action="authentication_failed",
    )
    with pytest.raises(DeviceRiskConnectorError, match="not_verified"):
        DeviceRiskClientFactory(store).for_merchant(store.get_merchant("merchant-a"))


def test_stub_mode_requires_no_connector(monkeypatch) -> None:
    assert store.create_merchant(_merchant("merchant-a"))
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "true")
    result = device.device_agent(_state("merchant-a"))
    assert result["device"] is not None
    assert result["evidence_collection_degraded"] is False


def test_api_state_audit_and_persisted_json_do_not_contain_plaintext_secret(
    client, tmp_path, caplog
) -> None:
    secret = "seon-plaintext-never-persist"
    _create_merchant(client, "merchant-a")
    response = _connect(client, "merchant-a", secret)
    connector_id = response.json()["connector_id"]
    state = _state("merchant-a")
    assert store.create_dispute(state)
    store.claim_provider_event({
        "event_id": "event-seon-secret-test",
        "provider": "razorpay",
        "processing_state": "received",
        "event_data": {"connector_id": connector_id},
    })
    local_path = tmp_path / "normal-store.json"
    local = InMemoryStore(local_path)
    assert local.create_merchant(store.get_merchant("merchant-a"))
    local.configure_device_risk_connector(
        store.get_device_risk_connector("merchant-a", connector_id)
    )
    assert InMemoryStore(local_path).get_device_risk_connector(
        "merchant-a", connector_id
    ) is not None

    observable = json.dumps({
        "response": response.json(),
        "merchant": store.get_merchant("merchant-a"),
        "state": state,
        "dispute": store.get_dispute("cb-merchant-a"),
        "event": store.get_provider_event("event-seon-secret-test"),
        "audit": store.list_device_risk_connector_audit("merchant-a"),
    }, default=str) + local_path.read_text(encoding="utf-8") + caplog.text
    assert secret not in observable


def test_verify_is_non_billable_and_disconnect_deletes_secret(client) -> None:
    _create_merchant(client, "merchant-a")
    connector_id = _connect(client, "merchant-a", "seon-disconnect-key").json()["connector_id"]

    verified = client.post(
        f"/merchants/merchant-a/device-risk-connectors/{connector_id}/verify"
    )
    disconnected = client.delete(
        f"/merchants/merchant-a/device-risk-connectors/{connector_id}"
    )

    assert verified.json()["status"] == "verification_pending"
    assert verified.json()["last_error_code"] == "verification_requires_first_real_request"
    assert disconnected.json()["status"] == "disconnected"
    with pytest.raises(CredentialStoreError, match="credential_not_found"):
        credential_secret_store_from_env().get(connector_id)
    assert {item["action"] for item in store.list_device_risk_connector_audit("merchant-a")} >= {
        "configured", "verification_deferred", "disconnected"
    }


def test_invalid_connector_cannot_be_reported_as_verified(client) -> None:
    _create_merchant(client, "merchant-a")
    connector_id = _connect(client, "merchant-a", "seon-invalid-key").json()["connector_id"]
    store.update_device_risk_connector_status(
        "merchant-a",
        connector_id,
        status="invalid",
        last_error_code="provider_authentication_failed",
        audit_action="authentication_failed",
    )

    response = client.post(
        f"/merchants/merchant-a/device-risk-connectors/{connector_id}/verify"
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "device_risk_connector_invalid"


def test_device_risk_connector_routes_require_authentication(monkeypatch) -> None:
    monkeypatch.setenv("API_KEY", "test-api-key")
    assert TestClient(app).get("/merchants/missing/device-risk-connectors").status_code == 401


def test_invalid_payload_is_redacted(client) -> None:
    _create_merchant(client, "merchant-a")
    secret = "bad secret with spaces"
    response = _connect(client, "merchant-a", secret)
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_device_risk_connector_request"}
    assert secret not in response.text


def test_unexpected_connector_fields_are_rejected(client) -> None:
    _create_merchant(client, "merchant-a")
    response = client.post(
        "/merchants/merchant-a/device-risk-connectors/seon",
        json={"api_key": "seon-valid-looking-key", "base_url": "https://attacker.test"},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_device_risk_connector_request"}
