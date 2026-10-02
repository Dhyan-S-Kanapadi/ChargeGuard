"""Merchant-scoped Ethoca and Verifi connector lifecycle coverage."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from agents.evidence import consortium
from api import consortium_connectors
from api.local_store import InMemoryStore
from api.store import store
from api.webhooks import build_initial_state
from integrations.consortium_client_factory import ConsortiumClientFactory, ConsortiumConnectorError
from integrations.credential_secrets import (
    CredentialStoreError,
    credential_secret_store_from_env,
    reset_credential_secret_store_cache,
)
from main import app


PAYLOADS = {
    "ethoca": {"api_key": "synthetic-ethoca-secret", "base_url": "https://ethoca.test", "verification_payment_id": "pay_validation"},
    "verifi": {"api_key": "synthetic-verifi-secret", "base_url": "https://verifi.test", "verification_payment_id": "pay_validation"},
}


def merchant(identifier: str) -> dict:
    return {"merchant_id": identifier, "name": identifier, "vertical": "ecommerce",
            "freshdesk_domain": "", "average_order_value": 100.0, "chargeback_history_count": 0}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("CHARGEGUARD_AUTH_MODE", "operator")
    monkeypatch.setenv("API_KEY", "test-consortium-api-key")
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "false")
    monkeypatch.setenv("CHARGEGUARD_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("CHARGEGUARD_CREDENTIAL_STORE_PATH", str(tmp_path / "secrets.json"))
    monkeypatch.setattr(consortium_connectors, "verify_consortium_credentials", lambda *_: {"verified": True, "error_code": None})
    reset_credential_secret_store_cache()
    store.clear()
    store.create_merchant(merchant("merchant-a"))
    store.create_merchant(merchant("merchant-b"))
    with TestClient(app, headers={"X-API-Key": "test-consortium-api-key"}) as test_client:
        yield test_client
    store.clear()
    reset_credential_secret_store_cache()


def connect(client: TestClient, provider: str, merchant_id: str = "merchant-a", payload: dict | None = None):
    return client.post(f"/merchants/{merchant_id}/consortium-connectors/{provider}", json=payload or PAYLOADS[provider])


@pytest.mark.parametrize("provider", PAYLOADS)
def test_create_verify_list_rotate_disconnect_and_redact_credentials(client, provider, tmp_path):
    first = connect(client, provider).json()
    assert first["status"] == "verified"
    assert "api_key" not in first and "base_url" not in first
    assert client.get("/merchants/merchant-a/consortium-connectors").json() == [first]
    assert credential_secret_store_from_env().get(first["connector_id"])["api_key"] == PAYLOADS[provider]["api_key"]
    replacement_payload = {**PAYLOADS[provider], "api_key": "replacement-synthetic-secret"}
    second = connect(client, provider, payload=replacement_payload).json()
    assert store.get_consortium_connector("merchant-a", first["connector_id"])["status"] == "disconnected"
    with pytest.raises(CredentialStoreError):
        credential_secret_store_from_env().get(first["connector_id"])
    verify = client.post(
        f"/merchants/merchant-a/consortium-connectors/{second['connector_id']}/verify",
        json={"verification_payment_id": "pay_revalidate"},
    )
    assert verify.status_code == 200 and verify.json()["status"] == "verified"
    assert client.delete(f"/merchants/merchant-a/consortium-connectors/{second['connector_id']}").json()["status"] == "disconnected"
    with pytest.raises(CredentialStoreError):
        credential_secret_store_from_env().get(second["connector_id"])
    observable = json.dumps(client.get("/merchants/merchant-a/consortium-connectors").json()) + (tmp_path / "secrets.json").read_text()
    assert replacement_payload["api_key"] not in observable


@pytest.mark.parametrize("provider", PAYLOADS)
def test_cross_merchant_access_is_hidden_and_factory_refuses_spoofed_reference(client, provider):
    connector_id = connect(client, provider, "merchant-b").json()["connector_id"]
    path = f"/merchants/merchant-a/consortium-connectors/{connector_id}"
    assert client.post(path + "/verify", json={"verification_payment_id": "pay_test"}).status_code == 404
    assert client.delete(path).status_code == 404
    spoofed = {**merchant("merchant-a"), "consortium_connector_ids": {provider: connector_id}}
    with pytest.raises(ConsortiumConnectorError, match="not_found"):
        ConsortiumClientFactory(store).for_merchant(spoofed, provider)


def test_invalid_provider_configuration_has_no_secret(client, monkeypatch):
    monkeypatch.setattr(consortium_connectors, "verify_consortium_credentials", lambda *_: {"verified": False, "error_code": "provider_authentication_failed"})
    response = connect(client, "ethoca")
    assert response.status_code == 201
    connector = response.json()
    assert connector["status"] == "invalid"
    with pytest.raises(CredentialStoreError):
        credential_secret_store_from_env().get(connector["connector_id"])
    assert client.post("/merchants/merchant-a/consortium-connectors/ethoca", json={
        "api_key": "secret with spaces", "base_url": "http://localhost", "verification_payment_id": "pay_test"
    }).json() == {"detail": "invalid_consortium_connector_request"}


def test_validation_uses_a_bounded_provider_lookup(monkeypatch):
    observed = {}

    class Ethoca:
        def __init__(self, *, api_key, base_url, timeout):
            observed.update(api_key=api_key, base_url=base_url, timeout=timeout)

        def search_alerts(self, identifiers):
            observed["identifiers"] = identifiers
            return {"match": False}

    monkeypatch.setattr(consortium_connectors, "EthocaClient", Ethoca)
    assert consortium_connectors.verify_consortium_credentials(
        "ethoca", "synthetic-key", "https://ethoca.test", "pay_validation"
    ) == {"verified": True, "error_code": None}
    assert observed == {"api_key": "synthetic-key", "base_url": "https://ethoca.test", "timeout": 10.0,
                        "identifiers": {"payment_id": "pay_validation"}}


def test_provider_failure_is_neutral_and_preserves_other_provider_evidence(client, monkeypatch):
    connect(client, "ethoca")
    connect(client, "verifi")

    class Ethoca:
        def search_alerts(self, identifiers):
            assert identifiers["merchant_id"] == "merchant-a"
            return {"alerts": [{"id": "alert"}], "dispute_count": 2, "echo": "synthetic-ethoca-secret"}

    def resolve(_merchant, provider):
        if provider == "ethoca":
            return Ethoca()
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(consortium.consortium_client_factory, "for_merchant", resolve)
    state = build_initial_state(
        chargeback_id="consortium-case", order_id="order-1", payment_id="pay-1", reason_code="10.4",
        card_network="VISA", dispute_amount=100.0, currency="INR",
        filing_deadline=datetime.now(timezone.utc) + timedelta(days=10), merchant_profile=merchant("merchant-a"),
    )
    result = consortium.consortium_agent(state)
    evidence = result["consortium"]
    assert evidence["lookup_complete"] is False
    assert evidence["ethoca_match"] is True
    assert evidence["verifi_match"] is False
    assert evidence["dispute_count_across_merchants"] == 2
    assert evidence["raw"]["source_errors"] == {"verifi": "verifi_provider_unavailable"}
    assert evidence["raw"]["providers"] == {
        "ethoca": {"match": True, "dispute_count": 2}, "verifi": {"match": False, "dispute_count": 0}
    }
    assert "synthetic-ethoca-secret" not in json.dumps(result, default=str)
    assert "verifi_provider_unavailable" in result["degraded_reasons"]


def test_local_persistence_restart_keeps_metadata_but_not_credentials(client, tmp_path):
    connector = connect(client, "ethoca").json()
    local = InMemoryStore(tmp_path / "metadata.json")
    local.create_merchant(merchant("merchant-a"))
    item = store.get_consortium_connector("merchant-a", connector["connector_id"])
    assert item is not None
    local.create_consortium_connector(item, audit_action="created")
    local.activate_consortium_connector(item, audit_action="verified")
    restarted = InMemoryStore(tmp_path / "metadata.json")
    assert restarted.get_consortium_connector("merchant-a", connector["connector_id"])["status"] == "verified"
    assert PAYLOADS["ethoca"]["api_key"] not in (tmp_path / "metadata.json").read_text()
