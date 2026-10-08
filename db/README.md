# PostgreSQL application store

Production selects `CHARGEGUARD_STORE_BACKEND=postgres` and requires `DATABASE_URL`
from the service secret manager. Non-production may select `local`; the legacy
development reviewer deployment keeps its local default for compatibility.
Do not change the deployed demo's environment or point it at a production DB.

This backend is implemented for staged testing, **not permission to launch**.
PostgreSQL does not solve the outstanding identity, secret-manager, artifact,
financial-safety, approval and release-control tasks.

## Caller map and preserved contract

`api/store.py` remains the import boundary. `api/local_store.py` holds the existing
domain transitions and local/demo persistence; `db/postgres.py` reloads relational
rows into a temporary unit of work under a PostgreSQL transaction lock. Only
changed rows and newly appended audits are persisted, then committed atomically.
It never saves the application as a single JSON blob and has no authoritative
process cache. Exceptions roll back the entire operation. DB errors sent to API
clients are generic and do not include connection strings, SQL values or rows.
The database adapter explicitly allowlists the current store operations. A new
local-store operation must have its database contract reviewed and tested too.
Connection loss during commit can leave the result uncertain: reload/check
persisted identity before retrying; do not assume an error proves a rollback.

| Store operations | Actual callers |
| --- | --- |
| Merchant create/update/list/account resolution | `api/merchants.py`, demo bootstrap, Razorpay processor/admin/service; connector APIs and factories |
| Payment connector create/activate/status/disconnect/audit | `api/payment_connectors.py`, `integrations/payment_client_factory.py` |
| Device connector candidate/activate/status/disconnect/audit | `api/device_risk_connectors.py`, `agents/evidence/device.py`, device-risk factory |
| Orders, exact identifiers, disputed flag, history queries | order ingestion, Shopify sync, order correlation, purchase-history agent, simulator |
| Dispute create/read/update | internal/provider webhooks, provider service, dispute APIs, public demo, stats/assistant, merchant analytics |
| Classification suggestion/rejection/claim | `api/disputes.py`; existing suggestion/approval predicates preserved |
| Provider-event claim/job lease/retry/status/recovery | signed receiver, Razorpay worker, reconciliation, recovery/admin |
| Simulator fixture lifecycle | simulator, public demo, scenario lookup; forbidden for production DB writes |

Typed columns persist merchant/order/connector identities and lifecycle fields.
Merchant connector links and transaction volumes use separate relational tables.
JSONB is reserved for flexible workflow/provider/fixture snapshots and structured
shipping addresses. Foreign keys enforce tenant ownership; unique constraints
enforce provider payment/order identifiers and active connector ownership.
Commerce order numbers retain the current contract: ambiguity returns no match,
rather than assuming storefront display numbers are globally unique.

Dispute snapshots expose an internal `_store_version`; a stale state write fails
instead of overwriting a newer snapshot. Callers must reload after a conflict.
Provider-event reclaims reject changed identity/payload. The API maps conflicts
to 409. Razorpay's signed receiver transactionally creates a `provider_events`
record and exactly one `provider_event_jobs` row. The PostgreSQL worker leases
one due row, bounds exponential retries, recovers expired leases, and fences
event/job completion with the lease token. It is at-least-once processing—not
permission to treat downstream provider or graph side effects as exactly once.

## Razorpay worker

With PostgreSQL selected and migrations applied, run the API and this separate
process against the same database:

```text
python -m api.razorpay_worker
```

It backfills recoverable Razorpay records written before it started, then polls
the durable table. `PROVIDER_EVENT_MAX_ATTEMPTS` (default `5`),
`PROVIDER_EVENT_RETRY_BASE_SECONDS` (default `5`, capped exponential delay),
and `PROVIDER_EVENT_WORKER_POLL_SECONDS` (default `1`) are bounded safeguards.
The local JSON/demo store retains its single-process background wake-up solely
for compatibility; never run this worker against it.

### Intentional limits

- The initial adapter serializes operations and scans current rows to reuse
  the existing financial transition logic. This is cross-process safe but has an
  **O(total records)** and single-writer throughput ceiling. Replace hot paths
  with scoped SQL/row locks and benchmark before a high-volume release.
- Connections are bounded by connect/statement/lock timeouts and closed after
  each operation. Pooling is not yet introduced; measure before adding it.
- Storefront credentials cannot be persisted in PostgreSQL. The current profile
  secret path fails closed until stage 4 provides the chosen managed backend.
  Payment/SEON secrets still use the separate encrypted-file boundary.
- Database migrations do not make local ML files or generated PDFs durable.
- Production credentials must use TLS with hostname/certificate verification
  (`sslmode=verify-full` and the database provider's CA as required), a private
  network where supported, a restricted application role, and separate migration
  credentials. Do not use the disposable test superuser in a real deployment.

## Explicit migration and import

Run from the repository with `ENVIRONMENT` and `DATABASE_URL` set in your local
process via approved secret handling. Never pass a real URL on the command line.

```text
python -m db.migrate                 # shows pending versions; no schema writes
python -m db.migrate --apply         # operator-authorized schema change
python -m db.import_json snapshot.json              # rollback-only import validation
python -m db.import_json snapshot.json --apply      # explicit all-or-nothing import
```

Migrations are checksummed, transactionally applied and environment-bound. No
migration runs automatically on app startup; startup checks for pending versions.
Checksums normalize text line endings for Windows/Linux consistency. Never edit
an applied migration; add the next numbered SQL file.

Imports preserve the source, default to dry-run, validate real relational
constraints, and report counts plus safe conflict categories/record positions.
Identical existing records are no-ops; conflicting records prevent the import.
Legacy untagged **non-production** files require an explicit
`--source-environment development` (or test/demo/staging) assertion. Untagged,
foreign-environment or synthetic snapshots must never enter production.
Do not relabel test data to bypass this guard. Raw storefront secrets cause a
conflict and require the separate secret-migration workflow.

## Disposable test database

Only set `CHARGEGUARD_TEST_DATABASE_URL` to a database created solely for tests.
The database suite clears application tables in that environment before/after
tests; it refuses a database labelled as any other environment.

```text
python -m pytest -q tests/test_postgres_store.py
```

Without the explicit test URL these tests skip, rather than silently using mocks.
CI provides a PostgreSQL service and runs both database tests and existing API,
connector, webhook, recovery and classification suites on it. Provider HTTP calls
in those tests remain mocked or synthetic; no live provider quota is consumed.

## Backup and restoration runbook (operator verification outstanding)

1. Enable the chosen database service's encrypted automated backups and PITR;
   set retention and recovery objectives with the operator. This code provisions
   nothing and does not prove those settings are enabled.
2. For an explicit export, use PostgreSQL `pg_dump --format=custom --file=...`
   with a protected service/password file or secret-manager injection. Never
   put credentials in the filename, Git, shell history or logs. Restrict access
   to the dump; it contains sensitive application/evidence snapshots.
3. Restore with `pg_restore --no-owner --no-acl --exit-on-error` into a **new,
   isolated empty database**, not over the live service. Use a matching client
   version, validate role grants, and leave workers/provider egress disabled.
4. Verify environment metadata, migration checksums, row counts, foreign keys,
   connector links, representative dispute state and provider-event claims.
   Correlate separately backed-up secrets, objects and model versions.
5. Exercise the recovery path offline. Record recovery time and recovered data
   range. Only approve cutover once the operator reviews evidence and rollback.

Rollback means returning to the known-good service/database pairing or restoring
a verified backup. Do not downgrade by deleting migration rows, truncate a live
DB, or overwrite the original JSON source. Database changes and restore drills
need separate operational authorization.

Implementation references: [Psycopg transactions](https://www.psycopg.org/psycopg3/docs/basic/transactions.html),
[PostgreSQL locking](https://www.postgresql.org/docs/current/explicit-locking.html),
[PostgreSQL constraints](https://www.postgresql.org/docs/current/ddl-constraints.html).
