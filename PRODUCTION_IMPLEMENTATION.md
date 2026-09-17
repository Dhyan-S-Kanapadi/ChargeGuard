# Production implementation ledger

This is a staged implementation, not a production-readiness claim. The public
reviewer deployment remains unchanged. No deployment, migration of real data,
live provider request, commit or push is part of this work.

## 1. Environment boundary

Explicit profiles: development, test, demo, staging, production. The existing
reviewer configuration still runs in development with its separate demo-session
authentication and opt-in guards. Synthetic CI runs use staging, not production.

Startup rejects unknown profiles, malformed safety flags, and missing deployment
authentication/webhook settings. Production additionally rejects demo access,
seeding, simulator execution, synthetic evidence and global credential fallbacks;
it requires distinct secrets (at least 32 characters), valid Fernet configuration,
PostgreSQL selection/URL and an absolute isolated credential path. Secret values are never
included in these validation errors. Length checks do not prove key entropy.

Production live evidence requires `CHARGEGUARD_LIVE_EVIDENCE_PROVIDERS`. The empty
default enables no provider. The protected `GET /internal/runtime` reports
`live_enabled`, `unavailable`, `unsupported`, or non-production `synthetic` modes.
Enabled does **not** mean account permissions or provider interoperability have
been verified. The generic food-platform adapter is unsupported in production.
Advisory chat/review configuration is separate and cannot authorize filing.

States carry their source environment. Production rejects untagged or foreign
workflow state and reserved simulator data at ingress and execution boundaries.
JSON snapshots also carry an environment label; production cannot open an
untagged/foreign/synthetic snapshot. Existing untagged local demo snapshots remain
readable in non-production. Labels are a guard against accidental mixing, not a
cryptographic provenance proof: never share stores, secrets, models or buckets
between environments. Do not relabel a demo snapshot to migrate it.

The filing stub is blocked in production even when quality passes. This does not
implement approval or provider submission. Use the separate
`deploy/production.env.example` only as an intent template; leave `render.yaml`
and the deployed reviewer service unchanged.

## Remaining stages, in order

| Stage | Status / prerequisite |
| --- | --- |
| 2. PostgreSQL | Implemented and tested on disposable PostgreSQL, including actual container restart persistence; see `db/README.md` for throughput and operational limits |
| 3. Identity and merchant authorization | Implemented and offline-tested with Supabase-compatible JWT verification, PostgreSQL memberships, role checks, tenant filters, session revocation, and dashboard login; Supabase project/MFA/recovery setup and live checks remain outstanding; see `SUPABASE_AUTH_SETUP.md` |
| 4. Managed connector secrets | Outstanding; managed backend choice required |
| 5. Private immutable artifacts | Outstanding; builds on DB and authorization |
| 6. Durable jobs and recovery | Outstanding; builds on DB |
| 7. Money, deadlines, missing evidence | Outstanding; verified rules and regression tests |
| 8. Narrow live validation | Outstanding; offline contracts first, live use opt-in only |
| 9. Human approval and manual submission | Outstanding; immutable artifacts and reviewer permissions |
| 10. Provider submission | Outstanding; disabled until all preceding stages pass |
| 11. Model/LLM releases | Outstanding; representative real evaluation data required |
| 12. Operations and launch checks | Outstanding; restoration and recovery evidence required |

Current launch assessment: **no-go** for real chargeback handling, including a
human-reviewed production pilot. Environment checks alone do not replace durable
transactions, tenant authorization, evidence integrity, operational readiness or
verified provider contracts. Autonomous production is also **no-go**.

## Verification checkpoint — 2026-09-17

- Before stage 3: full backend suite with isolated PostgreSQL: **467 passed**.
- Database restart: synthetic merchant probe survived an actual restart of the
  task-owned PostgreSQL container. Docker changed the host port; the initial run
  against the previous port had connection errors and was rerun successfully.
- First stage-3 pass: **35 identity tests passed**, using real signed JWTs and
  PostgreSQL for merchant isolation, roles, revocation, concurrency and outages.
- Existing local API/public-demo compatibility: **31 passed**.
- Frontend checks: **37 passed**; lint and production build passed. The build has
  a non-blocking bundle-size warning that should be addressed with route-level
  splitting before a public production launch.
- Final combined backend suite: **506 passed** using the isolated PostgreSQL
  instance. The Poetry module entry point was used because the Windows
  `pytest.exe` wrapper is blocked by Device Guard.
- No real database migration, provider call, Supabase provisioning, or deployment
  has occurred. Secret files and the Render demo are unchanged.
