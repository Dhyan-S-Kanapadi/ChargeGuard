"""Managed Gmail/Freshdesk: real routes and encryption, mocked provider HTTP."""
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from agents.evidence import comms
from api import support_connectors
from api.identity import authorize_route
from api.local_store import InMemoryStore
from api.store import store
from api.webhooks import build_initial_state
from integrations.credential_secrets import (
    CredentialStoreError, FernetFileCredentialSecretStore,
    credential_secret_store_from_env, reset_credential_secret_store_cache,
)
from integrations.support_client_factory import SupportClientFactory, SupportConnectorError
from main import app

PAYLOADS = {
    "gmail": {"access_token": "synthetic-gmail-secret"},
    "freshdesk": {"api_key": "synthetic-freshdesk-secret", "domain": "sample.freshdesk.com"},
}


def merchant(identifier):
    return {"merchant_id": identifier, "name": identifier, "vertical": "ecommerce",
            "freshdesk_domain": "", "average_order_value": 100., "chargeback_history_count": 0}



@pytest.fixture
def configured(monkeypatch, tmp_path):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("CHARGEGUARD_AUTH_MODE", "operator")
    monkeypatch.setenv("API_KEY", "test-support-api-key")
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "false")
    monkeypatch.delenv("GMAIL_USE_STUBS", raising=False)
    monkeypatch.delenv("FRESHDESK_USE_STUBS", raising=False)
    monkeypatch.setenv("CHARGEGUARD_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("CHARGEGUARD_CREDENTIAL_STORE_PATH", str(tmp_path / "secrets.json"))
    store.clear()
    reset_credential_secret_store_cache()
    for identifier in ("a", "b"):
        store.create_merchant(merchant(identifier))
    modes = {"gmail": 200, "freshdesk": 200}
    observed = []
    original = httpx.Client._send_single_request

    def send(client, request, **kwargs):
        if request.url.host == "testserver":
            return original(client, request, **kwargs)
        assert request.url.host in {"gmail.googleapis.com", "sample.freshdesk.com"}
        provider = "gmail" if request.url.host == "gmail.googleapis.com" else "freshdesk"
        observed.append(request)
        mode = modes[provider]
        if mode == "timeout":
            raise httpx.ReadTimeout("secret echoed in exception", request=request)
        if mode == "malformed":
            return httpx.Response(200, text="not json with secret", request=request)
        if mode == "wrong_shape":
            return httpx.Response(200, json=[] if provider == "gmail" else {}, request=request)
        if callable(mode):
            return mode(request)
        if mode != 200:
            return httpx.Response(mode, text=json.dumps(PAYLOADS), request=request)
        return httpx.Response(200, json={"messages": []} if provider == "gmail" else [], request=request)

    monkeypatch.setattr(httpx.Client, "_send_single_request", send)
    with TestClient(app, headers={"X-API-Key": "test-support-api-key"}) as client:
        yield client, modes, observed
    store.clear()
    reset_credential_secret_store_cache()


def connect(client, provider, owner="a", payload=None):
    return client.post(f"/merchants/{owner}/support-connectors/{provider}", json=payload or PAYLOADS[provider])


def connector_path(connector, owner="a"):
    return f"/merchants/{owner}/support-connectors/{connector['connector_id']}"


@pytest.mark.parametrize("provider", PAYLOADS)
def test_connect_list_encrypted_storage_restart_and_no_leaks(configured, provider, tmp_path, caplog):
    client, _, observed = configured
    response = connect(client, provider)
    assert response.status_code == 201
    connector = response.json()
    assert connector["status"] == "verified"
    assert connector["verified_at"]
    assert client.get("/merchants/a/support-connectors").json() == [connector]
    assert client.get("/merchants/b/support-connectors").json() == []
    credentials = credential_secret_store_from_env().get(connector["connector_id"])
    assert credentials == PAYLOADS[provider]
    reset_credential_secret_store_cache()
    assert credential_secret_store_from_env().get(connector["connector_id"]) == credentials
    local = InMemoryStore(tmp_path / "metadata.json")
    local.create_merchant(merchant("a"))
    local.save_support_connector(store.get_support_connector("a", connector["connector_id"]), audit_action="verified")
    restarted = InMemoryStore(tmp_path / "metadata.json")
    assert restarted.get_support_connector("a", connector["connector_id"])["status"] == "verified"
    observable = response.text + (tmp_path / "metadata.json").read_text() + (tmp_path / "secrets.json").read_text() + caplog.text
    secret = credentials.get("access_token") or credentials["api_key"]
    assert secret not in observable
    assert secret[-4:] not in response.text  # no partial secret hints
    if provider == "gmail":
        assert observed[-1].headers["Authorization"] == f"Bearer {secret}"
        assert observed[-1].url.path == "/gmail/v1/users/me/messages"
        assert observed[-1].url.params["maxResults"] == "1"
    else:
        assert observed[-1].headers["Authorization"] == "Basic " + base64.b64encode(f"{secret}:X".encode()).decode()
        assert observed[-1].url.params["per_page"] == "1"


@pytest.mark.parametrize("provider", PAYLOADS)
@pytest.mark.parametrize("mode,code", [(401, "provider_authentication_failed"), (403, "provider_authentication_failed"),
    (302, "provider_verification_failed"), (429, "provider_verification_failed"), (500, "provider_verification_failed"), ("timeout", "provider_unavailable"),
    ("malformed", "provider_verification_failed"), ("wrong_shape", "provider_verification_failed")])
def test_failed_creation_and_rotation_preserve_active(configured, provider, mode, code):
    client, modes, _ = configured
    modes[provider] = mode
    failed = connect(client, provider).json()
    assert failed["status"] == "invalid" and failed["last_error_code"] == code
    with pytest.raises(CredentialStoreError, match="credential_not_found"):
        credential_secret_store_from_env().get(failed["connector_id"])
    with pytest.raises(SupportConnectorError):
        SupportClientFactory(store).for_merchant(merchant("a"), provider)
    modes[provider] = 200
    working = connect(client, provider).json()
    modes[provider] = mode
    assert connect(client, provider).json()["status"] == "invalid"
    assert store.get_support_connector("a", working["connector_id"])["status"] == "verified"
    assert SupportClientFactory(store).for_merchant(merchant("a"), provider)


@pytest.mark.parametrize("provider", PAYLOADS)
def test_rotation_reverification_disconnection(configured, provider):
    client, modes, _ = configured
    original = connect(client, provider).json()
    key = "access_token" if provider == "gmail" else "api_key"
    rotated = connect(client, provider, payload={**PAYLOADS[provider], key: "replacement-synthetic-secret"}).json()
    assert store.get_support_connector("a", original["connector_id"])["status"] == "disconnected"
    with pytest.raises(CredentialStoreError):
        credential_secret_store_from_env().get(original["connector_id"])
    assert client.post(connector_path(original) + "/verify").status_code == 409
    for mode in ("timeout", 500, 429, "malformed"):
        modes[provider] = mode
        assert client.post(connector_path(rotated) + "/verify").json()["status"] == "verified"
    modes[provider] = 401
    assert client.post(connector_path(rotated) + "/verify").json()["status"] == "invalid"
    with pytest.raises(SupportConnectorError):
        SupportClientFactory(store).for_merchant(merchant("a"), provider)
    modes[provider] = 200
    assert client.post(connector_path(rotated) + "/verify").json()["status"] == "verified"
    for _ in range(2):
        assert client.delete(connector_path(rotated)).json()["status"] == "disconnected"
    with pytest.raises(CredentialStoreError):
        credential_secret_store_from_env().get(rotated["connector_id"])
    assert {a["action"] for a in store.list_support_connector_audit("a")} >= {
        "verified", "rotated", "rotated_out", "verification_failed", "verification_unavailable", "deleted"}


@pytest.mark.parametrize("provider", PAYLOADS)
def test_cross_merchant_and_no_global_fallback(configured, monkeypatch, provider):
    client, _, _ = configured
    a = connect(client, provider).json()
    key = "access_token" if provider == "gmail" else "api_key"
    b = connect(client, provider, "b", {**PAYLOADS[provider], key: "other-merchant-secret"}).json()
    assert client.post(connector_path(b) + "/verify").status_code == 404
    assert client.delete(connector_path(b)).status_code == 404
    factory = SupportClientFactory(store)
    assert getattr(factory.for_merchant(merchant("a"), provider), key) == PAYLOADS[provider][key]
    assert getattr(factory.for_merchant(merchant("b"), provider), key) == "other-merchant-secret"
    monkeypatch.setenv("GMAIL_ACCESS_TOKEN", "global-token")
    monkeypatch.setenv("FRESHDESK_API_KEY", "global-api-key")
    monkeypatch.setenv("FRESHDESK_DOMAIN", "global.freshdesk.com")
    monkeypatch.setenv("CHARGEGUARD_CONNECTOR_ACME_GMAIL_ACCESS_TOKEN", "legacy-token")
    client.delete(connector_path(a))
    profile = {**merchant("a"), "support_connector_ref": "ACME", "support_connector_ids": {provider: b["connector_id"]}}
    for profile in (profile, merchant("unregistered")):
        with pytest.raises(SupportConnectorError):
            factory.for_merchant(profile, provider)


@pytest.mark.parametrize("provider,payload", [
    ("gmail", {"access_token": "secret with whitespace"}),
    ("gmail", {"access_token": {"nested": "private-secret"}}),
    ("gmail", {"access_token": "valid-secret", "refresh_token": "private-secret"}),
    ("gmail", {"access_token": "valid-secret", "user_id": "other-mailbox"}),
    ("freshdesk", {"api_key": "valid-secret", "domain": "localhost"}),
    ("freshdesk", {"api_key": "valid-secret", "domain": "sample.freshdesk.com@evil.test"}),
    ("freshdesk", {"api_key": "valid-secret", "domain": "sample.freshdesk.com/path"}),
    ("freshdesk", {"api_key": "valid-secret", "domain": "sample.freshdesk.com.evil.test"}),
])
def test_validation_redacts_credentials_and_blocks_arbitrary_hosts(configured, provider, payload):
    client, _, observed = configured
    response = connect(client, provider, payload=payload)
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_support_connector_request"}
    assert not observed


def test_auth_unknown_merchant_and_malformed_body(configured):
    client, _, observed = configured
    assert TestClient(app).get("/merchants/a/support-connectors").status_code == 401
    assert connect(client, "gmail", owner="missing").status_code == 404
    assert client.get("/merchants/missing/support-connectors").status_code == 404
    response = client.post("/merchants/a/support-connectors/gmail", content='{"access_token":"secret', headers={"Content-Type": "application/json"})
    assert response.json() == {"detail": "invalid_support_connector_request"}
    assert not observed


@pytest.mark.parametrize("role,owner,expected", [("owner", "a", None), ("owner", "b", 404), ("reviewer", "a", 404), ("read_only", "a", 404)])
@pytest.mark.parametrize("method,template", [("POST", "/merchants/{merchant_id}/support-connectors/gmail"),
    ("POST", "/merchants/{merchant_id}/support-connectors/freshdesk"),
    ("POST", "/merchants/{merchant_id}/support-connectors/{connector_id}/verify"),
    ("DELETE", "/merchants/{merchant_id}/support-connectors/{connector_id}")])
def test_owner_only_management_authorization(role, owner, expected, method, template):
    request = Request({"type": "http", "method": method, "path": "/", "headers": [],
                       "query_string": b"", "route": SimpleNamespace(path=template),
                       "path_params": {"merchant_id": owner, "connector_id": "anything"}})
    principal = SimpleNamespace(platform_admin=False, aal="aal2", memberships={"a": role})
    if expected:
        with pytest.raises(HTTPException) as error:
            authorize_route(request, principal, {})
        assert error.value.status_code == expected
    else:
        assert authorize_route(request, principal, {}) == "a"


def state():
    result = build_initial_state(chargeback_id="support-case", order_id="order-1", payment_id="pay-1",
        reason_code="10.4", card_network="VISA", dispute_amount=100., currency="INR",
        filing_deadline=datetime.now(timezone.utc) + timedelta(days=30), merchant_profile=merchant("a"))
    result["transaction"] = {"customer_email": "buyer@example.test"}
    return result


def evidence_response(provider, request):
    if provider == "gmail":
        if request.url.path.endswith("/messages"):
            body = {"messages": [{"id": "message-1"}]}
        else:
            body = {"id": "message-1", "snippet": PAYLOADS["gmail"]["access_token"], "internalDate": "1700000000000",
                    "payload": {"headers": [{"name": "Subject", "value": "Private order-1"},
                                               {"name": "From", "value": "buyer@example.test"}],
                                "parts": [{"body": {"data": "PRIVATE-NESTED-CONTENT"}}]}}
    else:
        body = [{"id": 1, "subject": "Private order-1 " + PAYLOADS["freshdesk"]["api_key"],
                 "created_at": "2026-01-01T00:00:00Z", "description_text": "PRIVATE-NESTED-CONTENT",
                 "custom_fields": {"body": {"nested": PAYLOADS["freshdesk"]["api_key"]}},
                 "auth_echo": base64.b64encode((PAYLOADS["freshdesk"]["api_key"] + ":X").encode()).decode()}]
    return httpx.Response(200, json=body, request=request)


@pytest.mark.parametrize("failed", ["gmail", "freshdesk", None])
def test_independent_retrieval_secret_echoes_and_nested_api_redaction(configured, monkeypatch, failed, caplog):
    client, modes, _ = configured
    for provider in PAYLOADS:
        assert connect(client, provider).json()["status"] == "verified"
        modes[provider] = 500 if failed == provider else lambda req, p=provider: evidence_response(p, req)
    result = comms.comms_agent(state())
    assert bool(result["comms"]["emails"]) == (failed != "gmail")
    assert bool(result["comms"]["support_tickets"]) == (failed != "freshdesk")
    if failed:
        assert result["evidence_collection_degraded"]
        assert result["degraded_reasons"] == [failed + "_provider_unavailable"]
    observable = json.dumps(result, default=str) + caplog.text + json.dumps(store.list_support_connector_audit("a"), default=str)
    for payload in PAYLOADS.values():
        assert (payload.get("access_token") or payload["api_key"]) not in observable
    store.create_dispute(result)
    detail = client.get("/disputes/support-case")
    assert detail.status_code == 200
    assert set(detail.json()["state"]["comms"]) == {"post_delivery_interaction", "complaint_raised_before_chargeback"}
    assert "PRIVATE-NESTED-CONTENT" not in detail.text
    assert "Private order-1" not in detail.text
    assert "PRIVATE-NESTED-CONTENT" not in client.get("/disputes").text
    assert client.get("/disputes/support-case?include_raw=true").status_code == 403
    monkeypatch.setenv("INTERNAL_API_TOKEN", "internal-test-only")
    raw = client.get("/disputes/support-case?include_raw=true", headers={"X-Internal-Token": "internal-test-only"})
    assert raw.status_code == 200 and "PRIVATE-NESTED-CONTENT" in raw.text
    for payload in PAYLOADS.values():
        assert (payload.get("access_token") or payload["api_key"]) not in raw.text


@pytest.mark.parametrize("provider", PAYLOADS)
def test_secret_store_failures_fail_closed(configured, monkeypatch, provider, tmp_path):
    client, _, _ = configured
    connector = connect(client, provider).json()
    secret_store = credential_secret_store_from_env()
    secret_store._path.write_text("invalid json")
    with pytest.raises(CredentialStoreError, match="credential_store_unreadable"):
        SupportClientFactory(store).for_merchant(merchant("a"), provider)
    assert client.post(connector_path(connector) + "/verify").status_code == 409
    # Disconnect still revokes access if encrypted deletion cannot complete.
    assert client.delete(connector_path(connector)).status_code == 503
    assert store.get_support_connector("a", connector["connector_id"])["status"] == "disconnected"
    monkeypatch.delenv("CHARGEGUARD_CREDENTIAL_ENCRYPTION_KEY")
    assert connect(client, provider).status_code == 503


def test_wrong_key_and_tampering_cannot_decrypt(configured, tmp_path):
    client, _, _ = configured
    connector = connect(client, "gmail").json()
    other = FernetFileCredentialSecretStore(path=tmp_path / "secrets.json", encryption_key=Fernet.generate_key().decode())
    with pytest.raises(CredentialStoreError, match="credential_decryption_failed"):
        other.get(connector["connector_id"])
    path = tmp_path / "secrets.json"
    data = json.loads(path.read_text())
    data[connector["connector_id"]] = "tampered"
    path.write_text(json.dumps(data))
    with pytest.raises(CredentialStoreError, match="credential_decryption_failed"):
        credential_secret_store_from_env().get(connector["connector_id"])


@pytest.mark.parametrize("failure", ["metadata", "secret"])
def test_failed_rotation_storage_rolls_back_and_removes_candidate(configured, monkeypatch, failure):
    client, _, _ = configured
    original = connect(client, "gmail").json()
    before = deepcopy(store.list_support_connector_audit("a"))
    secrets = credential_secret_store_from_env()

    def fail(*args, **kwargs):
        raise CredentialStoreError("credential_store_write_failed") if failure == "secret" else OSError("disk full")

    monkeypatch.setattr(secrets if failure == "secret" else store, "put" if failure == "secret" else "_save", fail)
    if failure == "secret":
        assert connect(client, "gmail").status_code == 503
    else:
        with pytest.raises(OSError):
            connect(client, "gmail")
    assert store.get_support_connector("a", original["connector_id"])["status"] == "verified"
    assert store.list_support_connector_audit("a") == before
    assert list(secrets._read()) == [original["connector_id"]]
    monkeypatch.undo()


def test_stale_reverification_cannot_resurrect_disconnected_connector(configured):
    client, modes, _ = configured
    connector = connect(client, "gmail").json()

    def disconnect_during_request(request):
        assert client.delete(connector_path(connector)).status_code == 200
        return httpx.Response(200, json={"messages": []}, request=request)

    modes["gmail"] = disconnect_during_request
    assert client.post(connector_path(connector) + "/verify").status_code == 409
    assert store.get_support_connector("a", connector["connector_id"])["status"] == "disconnected"


def test_current_connector_resolution_ignores_stale_workflow_reference(configured):
    client, _, _ = configured
    first = connect(client, "gmail").json()
    stale = {**merchant("a"), "support_connector_ids": {"gmail": first["connector_id"]}}
    second = connect(client, "gmail", payload={"access_token": "new-synthetic-token"}).json()
    assert SupportClientFactory(store).for_merchant(stale, "gmail").access_token == "new-synthetic-token"
    client.delete(connector_path(second))
    with pytest.raises(SupportConnectorError):
        SupportClientFactory(store).for_merchant(stale, "gmail")


def test_factory_checks_ownership_even_with_bad_repository(configured):
    client, _, _ = configured
    connector = connect(client, "gmail", "b").json()
    bad_repository = SimpleNamespace(list_support_connectors=lambda _: [connector])
    with pytest.raises(SupportConnectorError):
        SupportClientFactory(bad_repository).for_merchant(merchant("a"), "gmail")


def test_demo_uses_real_clients_with_mocked_http():
    from scripts.support_connector_demo import provider_response
    from integrations.gmail_reader import GmailReader, GmailRequestError
    from integrations.freshdesk import FreshdeskClient
    with httpx.Client(transport=httpx.MockTransport(provider_response)) as client:
        GmailReader(access_token="demo-gmail-token", client=client).verify_credentials()
        FreshdeskClient(api_key="demo-freshdesk-key", domain="demo.freshdesk.com", client=client).verify_credentials()
        with pytest.raises(GmailRequestError) as error:
            GmailReader(access_token="demo-denied-token", client=client).verify_credentials()
        assert error.value.status_code == 401
