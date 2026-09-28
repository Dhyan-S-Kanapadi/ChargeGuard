from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import httpx
import pytest

from agents.evidence import shipping
from api import shipping_connectors
from api.store import store
from api.webhooks import build_initial_state
from integrations.credential_secrets import (
    CredentialStoreError,
    credential_secret_store_from_env,
    reset_credential_secret_store_cache,
)
from integrations.shipping_client_factory import ShippingClientFactory, ShippingConnectorError
from integrations.delhivery import DelhiveryRequestError
from integrations.shiprocket import ShiprocketRequestError
from main import app


SHIPROCKET = {"email": "ops@example.test", "password": "safe-password", "verification_tracking_id": "awb_123"}
DELHIVERY = {"api_token": "delhivery-token", "verification_tracking_id": "waybill_123"}


@pytest.fixture(autouse=True)
def connector_environment(tmp_path, monkeypatch):
    store.clear()
    reset_credential_secret_store_cache()
    monkeypatch.setenv("API_KEY", "test-api-key")
    monkeypatch.setenv("CHARGEGUARD_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("CHARGEGUARD_CREDENTIAL_STORE_PATH", str(tmp_path / "shipping-secrets.json"))
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "false")
    yield
    store.clear()
    reset_credential_secret_store_cache()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, headers={"X-API-Key": "test-api-key"})


def _merchant(merchant_id: str) -> dict:
    return {"merchant_id": merchant_id, "name": merchant_id, "vertical": "ecommerce",
            "freshdesk_domain": "", "average_order_value": 1_000.0, "chargeback_history_count": 0}


def _create_merchant(client: TestClient, merchant_id: str) -> None:
    assert client.post("/merchants", json=_merchant(merchant_id)).status_code == 201


def _verify(monkeypatch, provider: str, verified: bool = True) -> None:
    monkeypatch.setattr(
        shipping_connectors,
        f"verify_{provider}_credentials",
        lambda *_: {"verified": verified, "error_code": None if verified else "provider_authentication_failed"},
    )


def _connect(client: TestClient, monkeypatch, merchant_id: str, provider: str, payload: dict, verified: bool = True):
    _verify(monkeypatch, provider, verified)
    return client.post(f"/merchants/{merchant_id}/shipping-connectors/{provider}", json=payload)


def test_merchants_resolve_distinct_provider_credentials(client, monkeypatch) -> None:
    _create_merchant(client, "merchant-a")
    _create_merchant(client, "merchant-b")
    first = _connect(client, monkeypatch, "merchant-a", "shiprocket", SHIPROCKET)
    second = _connect(client, monkeypatch, "merchant-b", "delhivery", DELHIVERY)

    assert first.status_code == second.status_code == 201
    factory = ShippingClientFactory(store)
    assert factory.for_merchant(store.get_merchant("merchant-a"), "shiprocket").email == SHIPROCKET["email"]
    assert factory.for_merchant(store.get_merchant("merchant-b"), "delhivery").api_token == DELHIVERY["api_token"]


def test_invalid_rotation_keeps_existing_connector_and_secret(client, monkeypatch) -> None:
    _create_merchant(client, "merchant-a")
    original = _connect(client, monkeypatch, "merchant-a", "shiprocket", SHIPROCKET).json()
    failed = _connect(client, monkeypatch, "merchant-a", "shiprocket", {**SHIPROCKET, "password": "replacement-secret"}, False).json()

    assert failed["status"] == "invalid"
    assert store.get_merchant("merchant-a")["shipping_connector_ids"]["shiprocket"] == original["connector_id"]
    assert ShippingClientFactory(store).for_merchant(store.get_merchant("merchant-a"), "shiprocket").password == SHIPROCKET["password"]


def test_rotation_detaches_old_connector_only_after_activation(client, monkeypatch) -> None:
    _create_merchant(client, "merchant-a")
    original = _connect(client, monkeypatch, "merchant-a", "delhivery", DELHIVERY).json()
    replacement = _connect(client, monkeypatch, "merchant-a", "delhivery", {**DELHIVERY, "api_token": "replacement-token"}).json()

    assert replacement["status"] == "verified"
    assert store.get_shipping_connector("merchant-a", original["connector_id"])["status"] == "disconnected"
    with pytest.raises(CredentialStoreError, match="credential_not_found"):
        credential_secret_store_from_env().get(original["connector_id"])


def test_foreign_connector_is_hidden_and_factory_refuses_it(client, monkeypatch) -> None:
    _create_merchant(client, "merchant-a")
    _create_merchant(client, "merchant-b")
    connector_id = _connect(client, monkeypatch, "merchant-b", "shiprocket", SHIPROCKET).json()["connector_id"]

    assert client.get("/merchants/merchant-a/shipping-connectors").status_code == 200
    assert client.post(f"/merchants/merchant-a/shipping-connectors/{connector_id}/verify", json={"verification_tracking_id": "awb_123"}).status_code == 404
    profile = store.get_merchant("merchant-a")
    profile["shipping_connector_ids"] = {"shiprocket": connector_id}
    with pytest.raises(ShippingConnectorError, match="shipping_connector_not_found"):
        ShippingClientFactory(store).for_merchant(profile, "shiprocket")


def test_invalid_payload_and_responses_never_echo_credentials(client) -> None:
    _create_merchant(client, "merchant-a")
    secret = "secret should never echo"
    response = client.post("/merchants/merchant-a/shipping-connectors/shiprocket", json={
        "email": "not-an-email", "password": secret, "verification_tracking_id": "bad id with spaces",
    })
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_shipping_connector_request"}
    assert secret not in response.text


@pytest.mark.parametrize(
    "error,expected",
    [
        (ShiprocketRequestError("shiprocket_request_failed", status_code=401), "provider_authentication_failed"),
        (DelhiveryRequestError("delhivery_request_failed", status_code=403), "provider_authentication_failed"),
        (ShiprocketRequestError("shiprocket_request_failed", status_code=404), "provider_tracking_not_found"),
        (DelhiveryRequestError("delhivery_request_failed", status_code=429), "provider_verification_failed"),
    ],
)
def test_provider_statuses_are_sanitized_for_connector_verification(error, expected) -> None:
    assert shipping_connectors._verification_error(error) == {"verified": False, "error_code": expected}


def test_provider_timeout_degrades_verification_without_leaking_context(monkeypatch) -> None:
    class TimeoutClient:
        def __init__(self, **_):
            pass

        def get_tracking(self, _):
            raise httpx.TimeoutException("private provider response")

    monkeypatch.setattr(shipping_connectors, "ShiprocketClient", TimeoutClient)
    assert shipping_connectors.verify_shiprocket_credentials("ops@example.test", "safe-password", "awb_123") == {
        "verified": False, "error_code": "provider_unavailable"
    }


def test_live_evidence_uses_one_scoped_provider_and_keeps_no_raw_payload(client, monkeypatch) -> None:
    _create_merchant(client, "merchant-a")
    _connect(client, monkeypatch, "merchant-a", "shiprocket", SHIPROCKET)
    seen = []

    class FakeClient:
        def get_tracking(self, tracking_id):
            seen.append(tracking_id)
            return {"tracking_data": {"shipment_track": [{"awb_code": tracking_id, "courier_name": "Direct Courier", "current_status": "DELIVERED", "customer_phone": "private"}]}}

    monkeypatch.setattr(shipping.shipping_client_factory, "for_merchant", lambda merchant, provider: FakeClient())
    state = build_initial_state(
        chargeback_id="cb-shipping", order_id="order-1", payment_id="pay-1", reason_code="13.1", card_network="VISA",
        dispute_amount=10.0, currency="INR", filing_deadline=datetime.now(timezone.utc) + timedelta(days=5),
        merchant_profile=store.get_merchant("merchant-a"), tracking_id="awb_123", shipping_provider="shiprocket",
    )
    result = shipping.shipping_agent(state)

    assert seen == ["awb_123"]
    assert result["shipping"]["status"] == "DELIVERED"
    assert result["shipping"]["raw"] == {"source": "shiprocket"}
    assert "private" not in str(result["shipping"])
