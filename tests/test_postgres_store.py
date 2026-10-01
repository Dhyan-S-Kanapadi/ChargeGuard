"""Run only against an explicitly supplied disposable PostgreSQL database."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone

import pytest

from api.store import OrderIdentifierConflictError
from db.postgres import PostgresStore, StoreConflictError
from db.migrate import migrate


def process_claim(database_url):
    return PostgresStore(database_url, environment="test").claim_provider_event(
        {"event_id": "event_cross_process", "provider": "razorpay", "payload_hash": "same"})


@pytest.fixture
def pg(monkeypatch):
    url = os.getenv("CHARGEGUARD_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set CHARGEGUARD_TEST_DATABASE_URL to an isolated PostgreSQL test database")
    monkeypatch.setenv("ENVIRONMENT", "test")
    migrate(url, "test", apply=True)
    repository = PostgresStore(url, environment="test")
    repository.clear()
    yield repository
    repository.clear()


def merchant(identifier="merchant_a"):
    return {"merchant_id": identifier, "name": identifier, "vertical": "ecommerce",
            "average_order_value": 1000., "chargeback_history_count": 0,
            "freshdesk_domain": ""}


def order(identifier="order_a", owner="merchant_a", payment="pay_a"):
    return {"merchant_id": owner, "order_id": identifier, "provider_payment_id": payment,
            "customer_email": "shopper@example.invalid", "customer_ip": "192.0.2.5",
            "user_agent": "fixture", "shipping_address": {"city": "Example"},
            "order_date": datetime.now(timezone.utc), "is_disputed": False, "is_fraud_flagged": False}


def case():
    return {"chargeback_id": "dispute_a", "data_environment": "test",
            "merchant_profile": merchant(), "order_id": "order_a", "decision": None}


def test_restart_and_deep_copy(pg):
    assert pg.create_merchant(merchant())
    assert pg.upsert_order(order())
    snapshot = case()
    assert pg.create_dispute(snapshot)
    reopened = PostgresStore(pg.database_url, environment="test")
    assert reopened.get_order("merchant_a", "order_a")["is_disputed"] is True
    state = reopened.get_dispute("dispute_a")["state"]
    state["merchant_profile"]["name"] = "changed locally"
    assert pg.get_merchant("merchant_a")["name"] == "merchant_a"
    assert reopened.get_dispute("dispute_a")["state"]["merchant_profile"]["name"] == "merchant_a"


def test_concurrent_duplicate_and_processing_claims(pg):
    event = {"event_id": "event_a", "provider": "razorpay", "payload_hash": "hash_a"}
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(lambda _: PostgresStore(pg.database_url, environment="test").claim_provider_event(event), range(16))) == 1
    assert pg.queue_provider_event("event_a")
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(lambda _: pg.start_provider_event_processing("event_a"), range(16))) == 1
    assert pg.get_provider_event("event_a")["attempt_count"] == 1


def test_relational_ownership_and_identifier_conflicts(pg):
    pg.create_merchant(merchant())
    pg.create_merchant(merchant("merchant_b"))
    pg.upsert_order(order())
    with pytest.raises(OrderIdentifierConflictError):
        pg.upsert_order(order("order_b"))
    assert pg.get_order("merchant_a", "order_b") is None
    assert pg.upsert_order(order("order_b", "merchant_b"))
    with pytest.raises(StoreConflictError):
        pg.upsert_order(order("order_x", "missing_merchant", "pay_other"))


def test_consortium_connector_lifecycle_persists_and_enforces_ownership(pg):
    pg.create_merchant(merchant())
    pg.create_merchant(merchant("merchant_b"))
    now = datetime.now(timezone.utc)
    connector = {"connector_id": "consortium_one", "merchant_id": "merchant_a", "provider": "ethoca",
                 "status": "pending", "verified_at": None, "created_at": now, "updated_at": now,
                 "last_error_code": None}
    assert pg.create_consortium_connector(connector, audit_action="created")
    verified = {**connector, "status": "verified", "verified_at": now}
    assert pg.activate_consortium_connector(verified, audit_action="verified") is None
    restarted = PostgresStore(pg.database_url, environment="test")
    assert restarted.get_consortium_connector("merchant_a", "consortium_one")["status"] == "verified"
    assert restarted.get_consortium_connector("merchant_b", "consortium_one") is None
    assert restarted.get_merchant("merchant_a")["consortium_connector_ids"] == {"ethoca": "consortium_one"}


def test_concurrent_order_updates_preserve_flags(pg):
    pg.create_merchant(merchant())
    pg.upsert_order(order())
    with ThreadPoolExecutor(max_workers=2) as pool:
        actions = [pool.submit(pg.mark_order_disputed, "merchant_a", "order_a"),
                   pool.submit(pg.upsert_order, order())]
        for action in actions:
            action.result()
    assert pg.get_order("merchant_a", "order_a")["is_disputed"] is True


def test_transaction_failure_rolls_back_all_rows(pg, monkeypatch):
    pg.create_merchant(merchant())
    pg.upsert_order(order())
    original = pg._persist_changes
    def fail_after_write(connection, before, after):
        original(connection, before, after)
        raise RuntimeError("injected transaction failure")
    monkeypatch.setattr(pg, "_persist_changes", fail_after_write)
    with pytest.raises(RuntimeError, match="injected"):
        pg.create_dispute(case())
    assert pg.get_dispute("dispute_a") is None
    assert pg.get_order("merchant_a", "order_a")["is_disputed"] is False


def test_stale_dispute_snapshot_cannot_overwrite_newer_state(pg):
    pg.create_merchant(merchant())
    pg.create_dispute(case())
    first = pg.get_dispute("dispute_a")["state"]
    stale = pg.get_dispute("dispute_a")["state"]
    first["decision"] = "FIGHT"
    pg.update_dispute("dispute_a", status="completed", state=first)
    stale["decision"] = "ACCEPT"
    with pytest.raises(StoreConflictError):
        pg.update_dispute("dispute_a", status="completed", state=stale)
    assert pg.get_dispute("dispute_a")["state"]["decision"] == "FIGHT"


def test_migration_dry_run_idempotency_and_environment(pg):
    first = migrate(pg.database_url, "test", apply=False)
    assert first["pending"] == []
    assert migrate(pg.database_url, "test", apply=True)["applied"] == []
    with pytest.raises(ValueError, match="environment"):
        migrate(pg.database_url, "production", apply=True)


def test_json_import_is_dry_by_default_and_idempotent(pg, tmp_path):
    from db.import_json import import_snapshot
    path = tmp_path / "source.json"
    path.write_text(json.dumps({"data_environment": "test", "merchants": {"merchant_a": merchant()}}))
    original = path.read_bytes()
    assert import_snapshot(pg, path)["would_import"] == 1
    assert pg.get_merchant("merchant_a") is None
    assert import_snapshot(pg, path, apply=True)["imported"] == 1
    assert import_snapshot(pg, path, apply=True)["imported"] == 0
    assert path.read_bytes() == original


def test_duplicate_claims_across_independent_processes(pg):
    with ProcessPoolExecutor(max_workers=3) as pool:
        assert sum(pool.map(process_claim, [pg.database_url] * 6)) == 1


def test_connector_rotation_ownership_and_audit(pg):
    pg.create_merchant(merchant())
    pg.create_merchant(merchant("merchant_b"))
    now = datetime.now(timezone.utc)
    first = {"connector_id": "connector_a", "merchant_id": "merchant_a", "provider": "razorpay",
             "provider_account_id": "acc_a", "status": "verified", "credential_hint": "1234",
             "verified_at": now, "created_at": now, "updated_at": now, "last_error_code": None}
    assert pg.activate_payment_connector(first, audit_action="created") is None
    second = {**first, "connector_id": "connector_b"}
    assert pg.activate_payment_connector(second, audit_action="rotated") == "connector_a"
    assert pg.get_payment_connector("merchant_a", "connector_a")["status"] == "disconnected"
    assert pg.get_payment_connector("merchant_b", "connector_b") is None
    assert pg.get_merchant("merchant_a")["payment_connector_ids"] == {"razorpay": "connector_b"}
    with pytest.raises(ValueError):
        pg.activate_payment_connector({**second, "connector_id": "connector_c", "merchant_id": "merchant_b"}, audit_action="created")
    assert len(pg.list_payment_connector_audit("merchant_a")) == 3
    assert pg.list_payment_connector_audit("merchant_b") == []


def test_direct_sql_cannot_bypass_tenant_foreign_key(pg):
    import psycopg
    from db.migrate import connect
    pg.create_merchant(merchant())
    with pytest.raises(psycopg.IntegrityError):
        with connect(pg.database_url) as connection:
            connection.execute("INSERT INTO payment_connectors(connector_id,merchant_id,provider,status,created_at,updated_at) VALUES ('bad','missing','razorpay','pending',now(),now())")
    assert pg.list_payment_connectors("merchant_a") == []


def test_conflicting_import_never_partially_commits(pg, tmp_path):
    from db.import_json import import_snapshot
    pg.create_merchant(merchant())
    path = tmp_path / "conflicting.json"
    path.write_text(json.dumps({"data_environment": "test", "merchants": {
        "merchant_a": {**merchant(), "name": "wrong"}, "merchant_b": merchant("merchant_b")}}))
    result = import_snapshot(pg, path, apply=True)
    assert result["conflicts"]
    assert result["imported"] == 0
    assert pg.get_merchant("merchant_b") is None


def test_record_identity_cannot_move_between_merchants(pg):
    pg.create_merchant(merchant())
    pg.create_merchant(merchant("merchant_b"))
    with pytest.raises(StoreConflictError):
        pg.update_merchant("merchant_a", {"merchant_id": "merchant_b", "name": "bad"})
    assert pg.get_merchant("merchant_b")["name"] == "merchant_b"


def test_empty_optional_provider_identifiers_do_not_collide(pg):
    pg.create_merchant(merchant())
    assert pg.upsert_order(order(payment=""))
    assert pg.upsert_order(order("order_b", payment=""))


def test_database_unavailable_fails_without_secret(monkeypatch):
    from core.runtime import RuntimeConfigurationError
    import psycopg
    import db.postgres as module
    def unavailable(*args, **kwargs):
        raise psycopg.OperationalError("do-not-print-database-password")
    monkeypatch.setattr(module, "connect", unavailable)
    with pytest.raises(RuntimeConfigurationError) as error:
        PostgresStore("not-used", environment="test").list_merchants()
    assert "do-not-print" not in str(error.value)


def test_explicit_database_contract_covers_current_local_operations():
    from api.local_store import InMemoryStore
    from db.postgres import READS, WRITES
    public = {name for name in vars(InMemoryStore) if not name.startswith("_")}
    assert public - {"from_env", "clear"} == READS | WRITES


def test_event_owner_can_resolve_once_but_cannot_move(pg):
    pg.create_merchant(merchant())
    pg.create_merchant(merchant("merchant_b"))
    pg.claim_provider_event({"event_id": "event_owner", "provider": "razorpay"})
    pg.update_provider_event("event_owner", merchant_id="merchant_a", processing_state="updated")
    with pytest.raises(StoreConflictError):
        pg.update_provider_event("event_owner", merchant_id="merchant_b")
    assert pg.get_provider_event("event_owner")["merchant_id"] == "merchant_a"
