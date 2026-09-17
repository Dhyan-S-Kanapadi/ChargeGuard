"""Versioned, checksummed migrations. Nothing is applied at application startup."""
import argparse
import hashlib
import json
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from core.runtime import runtime_environment

MIGRATIONS = Path(__file__).with_name("migrations")
STORE_LOCK = 724906015


def connect(database_url):
    return psycopg.connect(database_url, connect_timeout=5, row_factory=dict_row,
                           options="-c search_path=chargeguard,pg_catalog -c statement_timeout=30000 -c lock_timeout=10000")


def migrate(database_url, environment, *, apply=False):
    environment = runtime_environment({"ENVIRONMENT": environment})
    with connect(database_url) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (STORE_LOCK,))
        exists = connection.execute("SELECT to_regclass('chargeguard.schema_migrations') AS name").fetchone()["name"]
        applied = {}
        if exists:
            saved = connection.execute("SELECT environment FROM store_environment WHERE singleton=true").fetchone()
            if not saved or saved["environment"] != environment:
                raise ValueError("Database environment mismatch; never share databases across profiles.")
            applied = {row["version"]: row["checksum"] for row in connection.execute("SELECT * FROM schema_migrations")}
        files = sorted(MIGRATIONS.glob("[0-9]*.sql"))
        known = {file.name for file in files}
        if set(applied) - known:
            raise ValueError("Database has migrations unknown to this release.")
        pending = []
        for file in files:
            checksum = hashlib.sha256(file.read_text(encoding="utf-8").encode()).hexdigest()
            if file.name in applied and applied[file.name] != checksum:
                raise ValueError("Applied migration checksum mismatch.")
            if file.name not in applied:
                pending.append(file.name)
        if not apply:
            connection.rollback()
            return {"pending": pending, "applied": []}
        if not exists:
            connection.execute("CREATE SCHEMA IF NOT EXISTS chargeguard")
            connection.execute("CREATE TABLE schema_migrations (version text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())")
            connection.execute("CREATE TABLE store_environment (singleton boolean PRIMARY KEY CHECK(singleton), environment text NOT NULL)")
            connection.execute("INSERT INTO store_environment VALUES (true,%s)", (environment,))
        for file in files:
            if file.name in pending:
                connection.execute(file.read_text(encoding="utf-8"))
                connection.execute("INSERT INTO schema_migrations(version,checksum) VALUES (%s,%s)",
                                   (file.name, hashlib.sha256(file.read_text(encoding="utf-8").encode()).hexdigest()))
        return {"pending": [], "applied": pending}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Explicitly apply pending migrations")
    args = parser.parse_args()
    url = os.getenv("DATABASE_URL")
    if not url:
        parser.error("DATABASE_URL is required (never pass credentials on the command line)")
    try:
        print(json.dumps(migrate(url, runtime_environment(), apply=args.apply)))
    except Exception:
        parser.exit(1, "Migration failed; check database access, environment and migration integrity. No credentials are printed.\n")


if __name__ == "__main__":
    main()
