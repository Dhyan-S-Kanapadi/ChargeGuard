"""Relational persistence with the existing domain transitions as a unit of work.

No authoritative process cache: every operation reloads committed database rows.
The PostgreSQL transaction lock, constraints, and commit provide cross-process
safety; the temporary domain object's RLock is NOT the database lock.
"""
from copy import deepcopy
from decimal import Decimal
from functools import partial
import json

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from api.local_store import (
    InMemoryStore, OrderIdentifierConflictError, _encode_json_value, _decode_json_value,
    _MERCHANT_CREDENTIAL_KEYS,
)
from core.runtime import RuntimeConfigurationError, contains_synthetic_data, assert_workflow_environment
from db.migrate import connect, STORE_LOCK


class StoreConflictError(ValueError):
    """Safe conflict category; never includes rows, credentials or SQL parameters."""


TABLES = {
    "merchants": ("merchant_id",), "payment_connectors": ("connector_id",),
    "shipping_connectors": ("connector_id",),
    "device_risk_connectors": ("connector_id",), "orders": ("merchant_id", "order_id"),
    "disputes": ("chargeback_id",), "provider_events": ("event_id",),
    "simulator_disputes": ("dispute_id",),
    "payment_connector_audit": (), "shipping_connector_audit": (), "device_risk_connector_audit": (),
}
# Explicit allowlist: a future local method needs a database-contract review.
READS = frozenset({
    "get_merchant", "get_merchant_by_razorpay_account_id", "get_merchant_by_payment_connector_account", "list_merchants",
    "get_payment_connector", "list_payment_connectors", "list_payment_connector_audit",
    "get_shipping_connector", "list_shipping_connectors", "list_shipping_connector_audit",
    "get_device_risk_connector", "list_device_risk_connectors", "list_device_risk_connector_audit",
    "get_order", "get_order_by_provider_payment_id", "get_order_by_provider_order_id",
    "get_order_by_commerce_order_number", "query_orders", "get_dispute", "list_disputes",
    "get_provider_event", "list_provider_events", "list_provider_events_for_dispute",
    "list_recoverable_provider_events", "get_simulator_dispute", "list_simulator_disputes",
})
WRITES = frozenset({
    "create_merchant", "update_merchant", "create_payment_connector", "activate_payment_connector",
    "update_payment_connector_status", "disconnect_payment_connector", "configure_device_risk_connector",
    "create_shipping_connector", "activate_shipping_connector", "update_shipping_connector_status",
    "disconnect_shipping_connector",
    "activate_device_risk_connector", "update_device_risk_connector_status", "disconnect_device_risk_connector",
    "upsert_order", "create_order", "mark_order_disputed", "create_dispute", "update_dispute",
    "claim_dispute_classification", "save_classification_suggestion", "reject_classification_suggestion",
    "claim_provider_event", "queue_provider_event", "start_provider_event_processing",
    "requeue_provider_event", "update_provider_event", "create_simulator_dispute", "update_simulator_dispute",
})
JSON_COLUMNS = {"state", "event_data", "snapshot", "shipping_address"}
EVENT_ALIASES = {"provider_event_id": "event_id", "event_name": "event_type",
                 "chargeback_id": "provider_dispute_id", "processing_status": "processing_state",
                 "error": "failure_reason"}


def _decode(value):
    return json.loads(json.dumps(value), object_hook=_decode_json_value)


class PostgresStore:
    def __init__(self, database_url, *, environment):
        self.database_url = database_url
        self.environment = environment

    def __getattr__(self, name):
        if name in READS | WRITES:
            return partial(self._execute, name)
        raise AttributeError(name)

    def _check_environment(self, connection):
        row = connection.execute("SELECT environment FROM store_environment WHERE singleton=true").fetchone()
        if not row or row["environment"] != self.environment:
            raise RuntimeConfigurationError("Database environment mismatch.")

    def check_ready(self):
        from db.migrate import migrate
        try:
            result = migrate(self.database_url, self.environment, apply=False)
            if result["pending"]:
                raise RuntimeConfigurationError("Database migrations are pending; apply them explicitly.")
        except psycopg.Error:
            raise RuntimeConfigurationError("Database is unavailable or uninitialized.") from None

    def _load_domain(self, connection):
        domain = InMemoryStore()
        for table, keys in TABLES.items():
            rows = connection.execute(sql.SQL("SELECT * FROM {} ORDER BY {}").format(
                sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, keys or ("audit_id",)))
            )).fetchall()
            result = {} if keys else []
            for row in rows:
                for key, value in row.items():
                    if key in JSON_COLUMNS and value is not None:
                        row[key] = _decode(value)
                    elif isinstance(value, Decimal):
                        row[key] = float(value)  # Existing API projection; money semantics are stage 7.
                if table == "disputes":
                    row.pop("merchant_id")
                    row["state"]["_store_version"] = row.pop("revision")
                elif table == "provider_events":
                    row.update({alias: row[column] for alias, column in EVENT_ALIASES.items()})
                elif table == "simulator_disputes":
                    row = row["snapshot"]
                row.pop("audit_id", None)
                if keys:
                    key = domain._order_key(row["merchant_id"], row["order_id"]) if table == "orders" else row[keys[0]]
                    result[key] = row
                else:
                    result.append(row)
            setattr(domain, "_" + table, result)
        for row in connection.execute("SELECT * FROM merchant_payment_links"):
            domain._merchants[row["merchant_id"]].setdefault("payment_connector_ids", {})[row["provider"]] = row["connector_id"]
        for row in connection.execute("SELECT * FROM merchant_shipping_links"):
            domain._merchants[row["merchant_id"]].setdefault("shipping_connector_ids", {})[row["provider"]] = row["connector_id"]
        for row in connection.execute("SELECT * FROM merchant_volumes"):
            domain._merchants[row["merchant_id"]].setdefault("transaction_volume_30d_by_network", {})[row["network"]] = row["transaction_count"]
        return domain

    @staticmethod
    def _snapshot(domain):
        return {name: deepcopy(getattr(domain, "_" + name)) for name in TABLES}

    def _execute(self, name, *args, **kwargs):
        try:
            with connect(self.database_url) as connection:
                # ponytail: serialize the existing multi-record domain transitions.
                # Ceiling: O(total records) per operation. Replace with scoped SQL
                # and row locks before a high-volume launch; no cached JSON blob.
                connection.execute("SELECT pg_advisory_xact_lock(%s)", (STORE_LOCK,))
                self._check_environment(connection)
                domain = self._load_domain(connection)
                before = self._snapshot(domain) if name in WRITES else None
                if name == "update_dispute" and kwargs.get("state") is not None:
                    previous = domain._disputes[args[0]]["state"]
                    supplied = kwargs["state"]
                    if supplied.get("_store_version") != previous.get("_store_version"):
                        raise StoreConflictError("Dispute changed; reload before applying this update.")
                    if supplied.get("merchant_profile", {}).get("merchant_id") != previous.get("merchant_profile", {}).get("merchant_id"):
                        raise StoreConflictError("Dispute merchant ownership cannot change.")
                if name == "claim_provider_event":
                    event = args[0]
                    previous = domain._provider_events.get(event.get("event_id") or event.get("provider_event_id"))
                    if previous and any(event.get(key) != previous.get(key) for key in ("provider", "payload_hash", "account_id", "provider_dispute_id")):
                        raise StoreConflictError("Provider event identity or payload changed.")
                result = getattr(domain, name)(*args, **kwargs)
                if name in WRITES:
                    after = self._snapshot(domain)
                    for identifier, record in after["disputes"].items():
                        old = before["disputes"].get(identifier)
                        version = old["state"].get("_store_version", 0) if old else 0
                        if old is None or record["state"] != old["state"]:
                            record["state"]["_store_version"] = version + 1
                        else:
                            record["state"]["_store_version"] = version
                    self._persist_changes(connection, before, after)
                    if before != after:
                        connection.execute("INSERT INTO store_audit(operation) VALUES (%s)", (name,))
                    if isinstance(result, dict) and result.get("chargeback_id") in after["disputes"] and "status" not in result:
                        result["_store_version"] = after["disputes"][result["chargeback_id"]]["state"]["_store_version"]
            # Only stamp the caller after a successful commit, for the graph it schedules.
            if name == "create_dispute" and result:
                args[0]["_store_version"] = 1
            if name == "update_dispute" and kwargs.get("state") is not None:
                kwargs["state"]["_store_version"] = after["disputes"][args[0]]["state"]["_store_version"]
            return result
        except psycopg.errors.UniqueViolation as exc:
            if exc.diag.constraint_name in {"orders_merchant_id_provider_payment_id_key", "orders_merchant_id_provider_order_id_key"}:
                raise OrderIdentifierConflictError("Provider identifier already belongs to another order.") from None
            raise StoreConflictError("A unique identity or provider ownership constraint failed.") from None
        except psycopg.IntegrityError:
            raise StoreConflictError("Database ownership or state constraint failed.") from None
        except psycopg.Error:
            raise RuntimeConfigurationError("Database operation failed; verify persisted state before retrying.") from None

    def _persist_changes(self, connection, before, after):
        if self.environment == "production":
            if after["simulator_disputes"] or contains_synthetic_data(after):
                raise RuntimeConfigurationError("Synthetic database writes are forbidden in production.")
            for record in after["disputes"].values():
                assert_workflow_environment(record["state"], environment=self.environment)
        columns = {}
        for row in connection.execute("SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='chargeguard'"):
            columns.setdefault(row["table_name"], set()).add(row["column_name"])
        for table, keys in TABLES.items():
            old, new = before[table], after[table]
            if keys:
                for identifier, record in new.items():
                    expected = InMemoryStore._order_key(record["merchant_id"], record["order_id"]) if table == "orders" else record[keys[0]]
                    if identifier != expected:
                        raise StoreConflictError("Record identity cannot change.")
                    if identifier in old and old[identifier].get("merchant_id") is not None and record.get("merchant_id") != old[identifier]["merchant_id"]:
                        raise StoreConflictError("Record merchant ownership cannot change.")
                if set(old) - set(new):
                    raise StoreConflictError("Record deletion requires an explicit retention workflow.")
                changed = [record for key, record in new.items() if old.get(key) != record]
                if table.endswith("connectors"):
                    changed.sort(key=lambda record: record["status"] == "verified")
            else:
                if new[:len(old)] != old:
                    raise StoreConflictError("Audit history is append-only.")
                changed = new[len(old):]
            for original in changed:
                record = deepcopy(original)
                if table == "merchants":
                    if any(record.get(key) for key in _MERCHANT_CREDENTIAL_KEYS):
                        raise StoreConflictError("Storefront secret persistence requires the managed secret backend (stage 4).")
                    for key in (*_MERCHANT_CREDENTIAL_KEYS, "payment_connector_ids", "shipping_connector_ids", "transaction_volume_30d_by_network"):
                        record.pop(key, None)
                elif table == "disputes":
                    record["merchant_id"] = record["state"]["merchant_profile"]["merchant_id"]
                    record["revision"] = record["state"].pop("_store_version")
                elif table == "provider_events":
                    for alias in EVENT_ALIASES:
                        record.pop(alias, None)
                elif table == "simulator_disputes":
                    record = {"dispute_id": record["dispute_id"], "merchant_id": record["merchant_id"],
                              "created_at": record["created_at"], "snapshot": record}
                elif table == "orders":
                    for field in ("provider_payment_id", "provider_order_id"):
                        if field in record and not record[field]:
                            record[field] = None
                if set(record) - columns[table]:
                    raise StoreConflictError(f"Unsupported fields for {table}; migration required.")
                self._upsert(connection, table, keys, record)
        for identifier, merchant in after["merchants"].items():
            previous = before["merchants"].get(identifier, {})
            for field, table, key, value in (
                ("payment_connector_ids", "merchant_payment_links", "provider", "connector_id"),
                ("shipping_connector_ids", "merchant_shipping_links", "provider", "connector_id"),
                ("transaction_volume_30d_by_network", "merchant_volumes", "network", "transaction_count"),
            ):
                mapping = merchant.get(field) or {}
                if mapping != (previous.get(field) or {}):
                    connection.execute(sql.SQL("DELETE FROM {} WHERE merchant_id=%s").format(sql.Identifier(table)), (identifier,))
                    for name, count in mapping.items():
                        self._upsert(connection, table, ("merchant_id", key), {"merchant_id": identifier, key: name, value: count})

    @staticmethod
    def _upsert(connection, table, keys, record):
        fields = list(record)
        values = [Jsonb(_encode_json_value(record[key])) if key in JSON_COLUMNS and record[key] is not None else record[key] for key in fields]
        query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, fields)),
            sql.SQL(",").join(sql.Placeholder() for _ in fields))
        if keys:
            assignments = sql.SQL(",").join(sql.SQL("{}=EXCLUDED.{}").format(sql.Identifier(key), sql.Identifier(key)) for key in fields if key not in keys)
            query += sql.SQL(" ON CONFLICT ({}) DO UPDATE SET {}").format(sql.SQL(",").join(map(sql.Identifier, keys)), assignments)
        connection.execute(query, values)

    def clear(self):
        if self.environment != "test":
            raise RuntimeConfigurationError("Database clearing is permitted only in an isolated test database.")
        with connect(self.database_url) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (STORE_LOCK,))
            self._check_environment(connection)
            tables = list(TABLES) + ["merchant_payment_links", "merchant_shipping_links", "merchant_volumes", "store_audit",
                                    "access_audit", "app_sessions", "merchant_memberships", "app_users"]
            connection.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY").format(sql.SQL(",").join(map(sql.Identifier, tables))))
