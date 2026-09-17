"""Explicit snapshot import, dry-run by default; the source is never modified."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path

import psycopg

from api.local_store import _decode_json_value
from core.runtime import runtime_environment
from db.migrate import connect, STORE_LOCK
from db.postgres import PostgresStore, TABLES, StoreConflictError


def _same_imported_record(existing, incoming):
    # Relational nullable columns may be absent in an older snapshot.
    return all((_same_imported_record(existing.get(key) or {}, value) if isinstance(value, dict)
                else existing.get(key) == value) for key, value in incoming.items()
               if key != "_store_version")


def import_snapshot(repository, path, *, apply=False, source_environment=None):
    payload = json.loads(Path(path).read_text(encoding="utf-8"), object_hook=_decode_json_value)
    if not isinstance(payload, dict):
        raise ValueError("Snapshot must be an object.")
    source = payload.get("data_environment") or source_environment
    if source != repository.environment:
        raise ValueError("Snapshot environment must match the isolated destination database.")
    if repository.environment == "production" and not payload.get("data_environment"):
        raise ValueError("Untagged snapshots cannot be imported into production.")
    with connect(repository.database_url) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (STORE_LOCK,))
        repository._check_environment(connection)
        before = repository._snapshot(repository._load_domain(connection))
        after = deepcopy(before)
        count = 0
        conflicts = []
        for table, keys in TABLES.items():
            incoming = payload.get(table, {} if keys else [])
            if not isinstance(incoming, dict if keys else list):
                raise ValueError(f"Invalid snapshot collection: {table}.")
            for key, record in incoming.items() if keys else enumerate(incoming):
                if not isinstance(record, dict):
                    raise ValueError(f"Invalid snapshot record: {table}.")
                record = deepcopy(record)
                if table == "disputes":
                    record["state"]["_store_version"] = 1
                if keys:
                    existing = before[table].get(key)
                    if existing:
                        if not _same_imported_record(existing, record):
                            # Report collection and ordinal, not identifiers or private data.
                            conflicts.append({"table": table, "record_number": list(incoming).index(key) + 1})
                        continue
                    after[table][key] = record
                else:
                    if any(_same_imported_record(item, record) for item in after[table]):
                        continue
                    after[table].append(record)
                count += 1
        if conflicts:
            connection.rollback()
            return {"would_import": count, "imported": 0, "conflicts": conflicts}
        # Dry-run exercises real database constraints inside a rolled-back transaction.
        try:
            with connection.transaction():
                repository._persist_changes(connection, before, after)
                connection.execute("SET CONSTRAINTS ALL IMMEDIATE")
        except (psycopg.IntegrityError, StoreConflictError):
            connection.rollback()
            return {"would_import": count, "imported": 0, "conflicts": [{"table": "snapshot", "reason": "ownership_schema_or_identity_conflict"}]}
        if apply:
            if count:
                connection.execute("INSERT INTO store_audit(operation) VALUES ('import_snapshot')")
        else:
            connection.rollback()
        return {"would_import": count, "imported": count if apply else 0, "conflicts": []}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--source-environment", choices=["development", "test", "demo", "staging"],
                        help="Explicit provenance assertion for legacy untagged NON-production snapshots")
    args = parser.parse_args()
    if not os.getenv("DATABASE_URL"):
        parser.error("DATABASE_URL is required")
    try:
        repository = PostgresStore(os.environ["DATABASE_URL"], environment=runtime_environment())
        result = import_snapshot(repository, args.source, apply=args.apply, source_environment=args.source_environment)
        print(json.dumps(result))
        if result["conflicts"]:
            parser.exit(1)
    except Exception:
        parser.exit(1, "Import failed; source preserved. Check environment, schema and database access.\n")


if __name__ == "__main__":
    main()
