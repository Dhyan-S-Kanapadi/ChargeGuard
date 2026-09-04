from datetime import datetime, timedelta, timezone
import json

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pytest

from agents.evidence import transaction
from api import payment_connectors, razorpay_admin, razorpay_service
from api.store import InMemoryStore, store
from api.webhooks import build_initial_state
from integrations.credential_secrets import (
    CredentialStoreError,
    FernetFileCredentialSecretStore,
    credential_secret_store_from_env,
    reset_credential_secret_store_cache,
)
from integrations.payment_client_factory import PaymentClientFactory, PaymentConnectorError
from integrations.razorpay import RazorpayClient
from integrations.razorpay_schemas import RazorpayWebhookEnvelope
from main import app


RAZORPAY_A = {
    "key_id": "rzp_test_MerchantA1",
    "key_secret": "merchant-a-secret",
    "razorpay_account_id": "acc_MerchantA1",
}
RAZORPAY_B = {
    "key_id": "rzp_test_MerchantB2",
    "key_secret": "merchant-b-secret",
    "razorpay_account_id": "acc_MerchantB2",
}


@pytest.fixture(autouse=True)
def connector_environment(tmp_path, monkeypatch):
    store.clear()
    reset_credential_secret_store_cache()
    monkeypatch.setenv("API_KEY", "test-api-key")
    monkeypatch.setenv("CHARGEGUARD_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("CHARGEGUARD_CREDENTIAL_STORE_PATH", str(tmp_path / "payment-secrets.json"))
    monkeypatch.delenv("ALLOW_GLOBAL_PAYMENT_CREDENTIAL_FALLBACK", raising=False)
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


def _verify_razorpay(monkeypatch, *, verified: bool = True) -> None:
    monkeypatch.setattr(
        payment_connectors,
        "verify_razorpay_credentials",
        lambda *_: {
            "verified": verified,
            "error_code": None if verified else "provider_authentication_failed",
            "provider_account_id": None,
        },
    )


def _connect_razorpay(
    client: TestClient,
    monkeypatch,
    merchant_id: str,
    credentials: dict = RAZORPAY_A,
    *,
    verified: bool = True,
):
    _verify_razorpay(monkeypatch, verified=verified)
    return client.post(
        f"/merchants/{merchant_id}/payment-connectors/razorpay",
        json=credentials,
    )


def test_merchants_resolve_distinct_razorpay_credentials(client, monkeypatch) -> None:
    _create_merchant(client, "merchant-a")
    _create_merchant(client, "merchant-b")
    first = _connect_razorpay(client, monkeypatch, "merchant-a", RAZORPAY_A)
    second = _connect_razorpay(client, monkeypatch, "merchant-b", RAZORPAY_B)

    assert first.status_code == second.status_code == 201
    factory = PaymentClientFactory(store)
    client_a = factory.for_merchant(store.get_merchant("merchant-a"), "razorpay")
    client_b = factory.for_merchant(store.get_merchant("merchant-b"), "razorpay")

    assert isinstance(client_a, RazorpayClient)
    assert isinstance(client_b, RazorpayClient)
    assert client_a.key_id == RAZORPAY_A["key_id"]
    assert client_b.key_id == RAZORPAY_B["key_id"]
    assert client_a.key_secret != client_b.key_secret


def test_cross_merchant_connector_access_is_rejected(client, monkeypatch) -> None:
    _create_merchant(client, "merchant-a")
    _create_merchant(client, "merchant-b")
    connector_id = _connect_razorpay(client, monkeypatch, "merchant-b").json()["connector_id"]

    assert client.post(
        f"/merchants/merchant-a/payment-connectors/{connector_id}/verify"
    ).status_code == 404
    profile = store.get_merchant("merchant-a")
    profile["payment_provider"] = "razorpay"
    profile["payment_connector_id"] = connector_id
    with pytest.raises(PaymentConnectorError, match="payment_connector_not_found"):
        PaymentClientFactory(store).for_merchant(profile)


def test_invalid_connection_and_failed_rotation_keep_working_connector(client, monkeypatch) -> None:
    _create_merchant(client, "merchant-a")
    original = _connect_razorpay(client, monkeypatch, "merchant-a").json()
    failed = _connect_razorpay(
        client,
        monkeypatch,
        "merchant-a",
        {**RAZORPAY_A, "key_secret": "replacement-secret"},
        verified=False,
    ).json()

    assert failed["status"] == "invalid"
    assert failed["last_error_code"] == "provider_authentication_failed"
    assert store.get_merchant("merchant-a")["payment_connector_id"] == original["connector_id"]
    assert PaymentClientFactory(store).for_merchant(
        store.get_merchant("merchant-a"), "razorpay"
    ).key_secret == RAZORPAY_A["key_secret"]


def test_successful_rotation_switches_atomically_and_audits_rotation(client, monkeypatch) -> None:
    _create_merchant(client, "merchant-a")
    original = _connect_razorpay(client, monkeypatch, "merchant-a").json()
    replacement = _connect_razorpay(
        client,
        monkeypatch,
        "merchant-a",
        {**RAZORPAY_A, "key_secret": "replacement-secret"},
    ).json()

    assert replacement["status"] == "verified"
    assert store.get_merchant("merchant-a")["payment_connector_id"] == replacement["connector_id"]
    assert store.get_payment_connector("merchant-a", original["connector_id"])["status"] == "disconnected"
    with pytest.raises(CredentialStoreError, match="credential_not_found"):
        credential_secret_store_from_env().get(original["connector_id"])
    assert "rotated" in {
        item["action"] for item in store.list_payment_connector_audit("merchant-a")
    }


def test_connector_status_update_rolls_back_when_metadata_save_fails(monkeypatch) -> None:
    local = InMemoryStore()
    assert local.create_merchant(_merchant("merchant-a"))
    connector = payment_connectors._metadata(
        merchant_id="merchant-a",
        provider="razorpay",
        provider_account_id="acc_Rollback",
        credential_hint="ending in back",
    )
    local.create_payment_connector(connector, audit_action="created")
    audit_before = local.list_payment_connector_audit("merchant-a")

    def fail_save() -> None:
        raise OSError("disk full")

    monkeypatch.setattr(local, "_save", fail_save)

    with pytest.raises(OSError, match="disk full"):
        local.update_payment_connector_status(
            "merchant-a",
            connector["connector_id"],
            status="invalid",
            last_error_code="provider_authentication_failed",
            audit_action="verification_failed",
        )

    saved = local.get_payment_connector("merchant-a", connector["connector_id"])
    assert saved is not None and saved["status"] == "pending"
    assert local.list_payment_connector_audit("merchant-a") == audit_before


def test_reverification_preserves_transient_failures_and_reactivates_valid_credentials(
    client,
    monkeypatch,
) -> None:
    _create_merchant(client, "merchant-a")
    connector_id = _connect_razorpay(client, monkeypatch, "merchant-a").json()[
        "connector_id"
    ]
    route = f"/merchants/merchant-a/payment-connectors/{connector_id}/verify"

    monkeypatch.setattr(
        payment_connectors,
        "verify_razorpay_credentials",
        lambda *_: {
            "verified": False,
            "error_code": "provider_verification_failed",
            "provider_account_id": None,
        },
    )
    transient = client.post(route)
    assert transient.json()["status"] == "verified"
    assert store.get_merchant("merchant-a")["payment_connector_id"] == connector_id

    _verify_razorpay(monkeypatch, verified=False)
    rejected = client.post(route)
    assert rejected.json()["status"] == "invalid"
    assert store.get_merchant("merchant-a")["payment_connector_id"] is None

    _verify_razorpay(monkeypatch)
    restored = client.post(route)
    assert restored.json()["status"] == "verified"
    assert store.get_merchant("merchant-a")["payment_connector_id"] == connector_id


def test_same_razorpay_account_cannot_attach_to_two_merchants(client, monkeypatch) -> None:
    _create_merchant(client, "merchant-a")
    _create_merchant(client, "merchant-b")
    assert _connect_razorpay(client, monkeypatch, "merchant-a").status_code == 201

    conflict = _connect_razorpay(
        client,
        monkeypatch,
        "merchant-b",
        {**RAZORPAY_B, "razorpay_account_id": RAZORPAY_A["razorpay_account_id"]},
    )

    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "razorpay_account_already_connected"
    assert store.get_merchant("merchant-b").get("payment_connector_id") is None


def test_secret_store_fails_closed_and_survives_restart(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CHARGEGUARD_CREDENTIAL_ENCRYPTION_KEY")
    with pytest.raises(CredentialStoreError, match="credential_encryption_key_missing"):
        credential_secret_store_from_env()
    with pytest.raises(CredentialStoreError, match="credential_encryption_key_invalid"):
        FernetFileCredentialSecretStore(path=tmp_path / "secrets.json", encryption_key="invalid")

    key = Fernet.generate_key().decode()
    path = tmp_path / "restarted-secrets.json"
    first = FernetFileCredentialSecretStore(path=path, encryption_key=key)
    first.put("paycon_restart", {"api_key": "sk_test_restartSecret"})
    raw = path.read_text(encoding="utf-8")
    second = FernetFileCredentialSecretStore(path=path, encryption_key=key)

    assert "sk_test_restartSecret" not in raw
    assert second.get("paycon_restart") == {"api_key": "sk_test_restartSecret"}


def test_connector_responses_state_logs_and_json_never_contain_secrets(
    client,
    monkeypatch,
    tmp_path,
    caplog,
) -> None:
    _create_merchant(client, "merchant-a")
    response = _connect_razorpay(client, monkeypatch, "merchant-a")
    merchant = store.get_merchant("merchant-a")
    state = build_initial_state(
        chargeback_id="cb-secret-test",
        order_id="order-1",
        payment_id="pay-1",
        reason_code="10.4",
        card_network="VISA",
        dispute_amount=100.0,
        currency="INR",
        filing_deadline=datetime.now(timezone.utc) + timedelta(days=10),
        merchant_profile=merchant,
    )
    assert store.create_dispute(state)
    store.claim_provider_event(
        {
            "event_id": "event-secret-test",
            "provider": "razorpay",
            "processing_state": "received",
            "event_data": {"account_id": RAZORPAY_A["razorpay_account_id"]},
        }
    )
    local_path = tmp_path / "normal-store.json"
    local = InMemoryStore(local_path)
    assert local.create_merchant(merchant)
    for connector in store.list_payment_connectors("merchant-a"):
        local.create_payment_connector(connector, audit_action="copied_for_test")

    observable = json.dumps(
        {
            "response": response.json(),
            "merchant": merchant,
            "state": state,
            "dispute": store.get_dispute("cb-secret-test"),
            "event": store.get_provider_event("event-secret-test"),
        },
        default=str,
    ) + local_path.read_text(encoding="utf-8") + caplog.text
    assert RAZORPAY_A["key_secret"] not in observable
    assert RAZORPAY_A["key_id"] not in observable


def test_disconnect_detaches_and_deletes_encrypted_secret(client, monkeypatch) -> None:
    _create_merchant(client, "merchant-a")
    connector_id = _connect_razorpay(client, monkeypatch, "merchant-a").json()["connector_id"]

    response = client.delete(f"/merchants/merchant-a/payment-connectors/{connector_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "disconnected"
    assert store.get_merchant("merchant-a")["payment_connector_id"] is None
    with pytest.raises(CredentialStoreError, match="credential_not_found"):
        credential_secret_store_from_env().get(connector_id)
    assert {entry["action"] for entry in store.list_payment_connector_audit("merchant-a")} >= {
        "created", "verified", "deleted"
    }


def test_global_fallback_requires_opt_in_and_never_masks_broken_connector(
    monkeypatch,
) -> None:
    assert store.create_merchant(_merchant("merchant-a"))
    profile = store.get_merchant("merchant-a")
    profile["payment_provider"] = "razorpay"
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_Global123")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "global-secret")
    with pytest.raises(PaymentConnectorError, match="payment_connector_not_configured"):
        PaymentClientFactory(store).for_merchant(profile)

    monkeypatch.setenv("ALLOW_GLOBAL_PAYMENT_CREDENTIAL_FALLBACK", "true")
    assert PaymentClientFactory(store).for_merchant(profile).key_id == "rzp_test_Global123"
    profile["payment_connector_id"] = "paycon_broken"
    profile["payment_connector_ids"] = {"razorpay": "paycon_broken"}
    with pytest.raises(PaymentConnectorError, match="payment_connector_not_found"):
        PaymentClientFactory(store).for_merchant(profile)

    assert store.create_merchant({**_merchant("merchant-invalid"), "payment_provider": "razorpay"})
    invalid = payment_connectors._metadata(
        merchant_id="merchant-invalid",
        provider="razorpay",
        provider_account_id="acc_Invalid",
        credential_hint="ending in lid1",
    )
    invalid["status"] = "invalid"
    invalid["last_error_code"] = "provider_authentication_failed"
    store.create_payment_connector(invalid, audit_action="verification_failed")
    with pytest.raises(PaymentConnectorError, match="payment_connector_not_verified"):
        PaymentClientFactory(store).for_merchant(
            store.get_merchant("merchant-invalid"), "razorpay"
        )


def test_transaction_agent_uses_merchant_scoped_factory(monkeypatch) -> None:
    observed = []

    class FakeClient:
        def get_payment(self, payment_id):
            return {"id": payment_id, "order_id": "order-1", "amount": 10000, "currency": "INR"}

        def get_order(self, order_id):
            return {"id": order_id}

    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "false")
    monkeypatch.setattr(
        transaction.payment_client_factory,
        "for_merchant",
        lambda merchant, provider: observed.append((merchant["merchant_id"], provider)) or FakeClient(),
    )
    state = build_initial_state(
        chargeback_id="cb-transaction",
        order_id="order-1",
        payment_id="pay-1",
        reason_code="10.4",
        card_network="VISA",
        dispute_amount=100.0,
        currency="INR",
        filing_deadline=datetime.now(timezone.utc) + timedelta(days=10),
        merchant_profile={**_merchant("merchant-a"), "payment_provider": "razorpay"},
    )

    result = transaction.transaction_agent(state)

    assert observed == [("merchant-a", "razorpay")]
    assert result["transaction"]["payment_id"] == "pay-1"


def test_stripe_transaction_uses_scoped_connector(client, monkeypatch) -> None:
    _create_merchant(client, "merchant-stripe")
    monkeypatch.setattr(
        payment_connectors,
        "verify_stripe_credentials",
        lambda *_: {"verified": True, "error_code": None, "provider_account_id": "acct_stripe"},
    )
    response = client.post(
        "/merchants/merchant-stripe/payment-connectors/stripe",
        json={"api_key": "sk_test_MerchantStripe1"},
    )

    observed = []

    class FakeStripeClient:
        def __init__(self, api_key):
            observed.append(api_key)

        def get_payment_intent(self, payment_id):
            return {
                "id": payment_id,
                "amount": 4200,
                "currency": "usd",
                "receipt_email": "stripe@example.test",
                "latest_charge": None,
                "status": "succeeded",
                "metadata": {"order_id": "order-stripe"},
            }

        def get_charge(self, charge_id):
            raise AssertionError("No charge lookup expected")

    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "false")
    monkeypatch.setattr(
        transaction,
        "payment_client_factory",
        PaymentClientFactory(
            store,
            stripe_builder=lambda **values: FakeStripeClient(values["api_key"]),
        ),
    )
    state = build_initial_state(
        chargeback_id="cb-stripe",
        order_id="order-stripe",
        payment_id="pi-stripe",
        reason_code="10.4",
        card_network="VISA",
        dispute_amount=42.0,
        currency="USD",
        filing_deadline=datetime.now(timezone.utc) + timedelta(days=10),
        merchant_profile=store.get_merchant("merchant-stripe"),
    )

    result = transaction.transaction_agent(state)

    assert response.status_code == 201
    assert observed == ["sk_test_MerchantStripe1"]
    assert result["transaction"]["amount"] == 42.0


def test_webhook_enrichment_and_reconciliation_receive_scoped_merchant(
    client,
    monkeypatch,
) -> None:
    _create_merchant(client, "merchant-a")
    _connect_razorpay(client, monkeypatch, "merchant-a")
    merchant = store.get_merchant("merchant-a")
    observed = []

    class FakeClient:
        def get_payment(self, payment_id, *, expand_card=False):
            observed.append(("payment", payment_id, expand_card))
            return {"id": payment_id, "order_id": "order-1", "method": "card", "card": {"network": "Visa"}}

        def list_disputes(self, **kwargs):
            observed.append(("reconcile", kwargs))
            return []

    monkeypatch.setattr(
        razorpay_service.payment_client_factory,
        "for_merchant",
        lambda profile, provider: observed.append((profile["merchant_id"], provider)) or FakeClient(),
    )
    envelope = RazorpayWebhookEnvelope.model_validate(
        {
            "entity": "event",
            "account_id": RAZORPAY_A["razorpay_account_id"],
            "event": "payment.dispute.created",
            "payload": {
                "payment": None,
                "dispute": {"entity": {
                    "id": "disp-scoped", "payment_id": "pay-scoped", "amount": 10000,
                    "currency": "INR", "reason_code": "unauthorised_transaction",
                    "respond_by": int((datetime.now(timezone.utc) + timedelta(days=5)).timestamp()),
                    "status": "open", "phase": "chargeback",
                }},
            },
        }
    )
    normalized = razorpay_service.normalize_with_enrichment(envelope, "event-scoped", merchant)
    assert normalized.card_network == "VISA"
    assert ("merchant-a", "razorpay") in observed

    monkeypatch.setattr(
        razorpay_admin.payment_client_factory,
        "for_merchant",
        lambda profile, provider: observed.append((f"reconcile:{profile['merchant_id']}", provider)) or FakeClient(),
    )
    response = client.post(
        "/internal/razorpay/reconcile",
        json={"merchant_id": "merchant-a", "count": 1},
    )
    assert response.status_code == 200
    assert ("reconcile:merchant-a", "razorpay") in observed


def test_connector_routes_require_authentication(monkeypatch) -> None:
    monkeypatch.setenv("API_KEY", "test-api-key")
    assert TestClient(app).get("/merchants/missing/payment-connectors").status_code == 401


def test_invalid_connector_payload_never_echoes_submitted_values(client) -> None:
    _create_merchant(client, "merchant-a")
    submitted_secret = "invalid secret value"

    response = client.post(
        "/merchants/merchant-a/payment-connectors/razorpay",
        json={
            "key_id": "not-a-key",
            "key_secret": submitted_secret,
            "razorpay_account_id": "not-an-account",
        },
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_payment_connector_request"}
    assert submitted_secret not in response.text
