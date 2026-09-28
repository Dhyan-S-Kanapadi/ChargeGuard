# ChargeGuard production implementation prompts

Use one prompt at a time. Do not start a later stage until the current stage is
implemented, tested, reviewed, and committed. The first two prompts are kept as
a record of the work already completed; start at Prompt 3 for the next change.

For a concise contributor-facing view of this sequence and its current boundary,
see [ROADMAP.md](ROADMAP.md).

## Shared instruction for every prompt

> Use the installed Ponytail skill in lite mode. Read `AGENTS.md`, `CLAUDE.md`,
> and `PONYTAIL.md` before changing code. Inspect the current implementation and
> relevant tests first. Preserve unrelated working-tree changes. Do not read,
> print, commit, or modify `.env` files or secrets. Use no real provider calls,
> production migrations, or deployment changes without asking first. Keep
> deterministic control over authentication, authorization, signatures, money,
> deadlines, case state, and filing. Add focused tests, run the full suite, run
> `git diff --check`, and report exactly what is complete and what remains.

---

## Prompt 1 — PostgreSQL application store — completed

> Implement the smallest safe migration path from ChargeGuard's local JSON store
> to PostgreSQL. Keep the existing store interface so API and workflow behavior
> remains unchanged. Add versioned SQL migrations, a repeatable migration runner,
> transaction-safe tenant-scoped constraints and idempotency protections, plus a
> one-time JSON import tool. Keep the local store for development/demo fallback.
> Add integration tests against an isolated disposable PostgreSQL database,
> including concurrent duplicate claims and restart persistence. Do not migrate a
> real database or change deployment configuration.

## Prompt 2 — Supabase authentication and merchant authorization — completed

> Implement Supabase Auth integration while retaining local API-key compatibility
> only for development/demo. Verify Supabase JWTs server-side using issuer,
> audience, expiration, and JWKS keys. Store user profiles, merchant memberships,
> session revocations, and access audit records in PostgreSQL. Enforce platform
> admin, merchant owner, analyst, and viewer permissions at every protected API
> route and filter list/read responses by merchant membership. Add dashboard login
> and logout using the official Supabase client. Test JWT validation, revoked
> sessions, role restrictions, cross-merchant denial, audit records, and fallback
> behavior. Do not provision a Supabase project or alter secrets.

## Prompt 3 — Managed connector-secret backend — next

> Replace the production use of file-based Fernet connector secrets with one
> managed secret backend, selected from the infrastructure already available to
> this project. Keep the existing `CredentialSecretStore` contract and local
> Fernet implementation for development only. Store only opaque secret references
> and safe metadata in PostgreSQL; never store provider credentials in API
> payloads, workflow state, logs, audit records, or backups. Add explicit secret
> versioning, rotation, deletion, fail-closed retrieval, and an auditable
> migration path from the existing encrypted file. Add mocked tests for ownership,
> unavailable-vault failure, rotation, rollback, and redaction. Before writing a
> cloud adapter or changing cloud configuration, ask which managed backend to use
> (for example AWS Secrets Manager, GCP Secret Manager, Azure Key Vault, or an
> existing vault).

## Prompt 4 — Immutable private evidence and rebuttal artifacts

> Move rebuttal PDFs, fact sidecars, and retained evidence attachments from local
> filesystem paths to a private object-storage abstraction. Use tenant- and
> case-scoped object keys, checksum every stored object, record immutable metadata
> in PostgreSQL, and expose only short-lived authorized download access. Preserve
> local filesystem storage solely for development. Ensure files cannot be swapped
> after quality approval or filing, and that cross-merchant access is denied.
> Add tests for checksum mismatch, missing object, expired access URL, tenant
> isolation, retention marking, and quality/filing behavior. Do not connect a real
> bucket or upload real evidence without approval.

## Prompt 5 — Durable webhook-to-workflow jobs and recovery

> Replace FastAPI in-process background scheduling with a durable PostgreSQL
> outbox/job table and a small worker process. A signed Razorpay webhook must be
> persisted and acknowledged before any provider enrichment or graph work starts.
> Implement transactional enqueue, leases, retries with bounded exponential
> backoff, idempotent job execution, stale-lease recovery, dead-letter state, and
> operator-safe retry. Preserve webhook HMAC verification, event ordering, and
> duplicate protections. Add tests for crash after acknowledgment, duplicate
> delivery, worker restart, concurrent workers, transient provider failure,
> permanent failure, and stale jobs. Do not add a queue service unless PostgreSQL
> outbox cannot safely meet a demonstrated requirement.

## Prompt 6 — Production observability and operational remediation

> Add structured, redacted logs and production metrics for HTTP requests, webhook
> verification, event state transitions, jobs, provider calls, graph outcomes,
> auth failures, and secret-store failures. Add correlation IDs that link a
> request, provider event, job, dispute, and audit record without exposing PII or
> secrets. Provide protected operator endpoints or documented SQL queries for
> dead-letter jobs, stuck events, failed workflows, and readiness checks. Define
> alert thresholds and a concise runbook. Test that logs and metrics redact
> credentials, customer identifiers, raw evidence, and provider payloads.

## Prompt 7 — Financial, deadline, and evidence correctness controls

> Harden production controls around money, filing deadlines, FX, and missing
> evidence. Define approved currency precision/rounding rules, make response-cost
> FX rates versioned and attributable, alert on stale or unknown rates, and fail
> safely to human review when automated economics are unreliable. Record the
> source and calculation inputs for deadlines and economics. Require evidence
> provenance, fetch time, provider status, and integrity checks; missing or
> contradictory evidence must remain neutral and lead to escalation rather than a
> fabricated decision. Add regression tests for currency precision, timezone and
> daylight-saving edges, overdue cases, missing FX, provider outage, and conflicting
> evidence. Do not let an LLM determine any of these values.

## Prompt 8 — Controlled Razorpay Test Mode certification

> Create a staging-only Razorpay Test Mode certification plan and implementation
> guardrails. Keep simulator support separate from real Test Mode. Require HTTPS,
> non-production keys, a separate webhook secret, verified merchant connector
> ownership, simulator disabled, and explicit environment allowlists. Add a
> documented checklist to prove exact-body HMAC verification, duplicate handling,
> delayed and out-of-order events, recovery, reconciliation, evidence collection,
> and final state recording using Test Mode only. Add contract tests around the
> outbound adapter with mocks. Do not enable live mode, submit a real dispute, or
> change provider dashboard settings without explicit approval.

## Prompt 9 — Human approval and real submission boundary

> Design and implement a two-person-review-ready submission workflow. A case may
> be submitted only when it has an immutable approved artifact, valid filing
> deadline, supported playbook, required evidence, merchant authorization, and
> an explicit human approval recorded in the audit trail. Keep the current local
> filing confirmation as the default. Build a disabled-by-default provider
> submission adapter behind explicit staging/production feature flags, idempotency
> keys, outcome polling, and safe failure states. Add tests proving that no LLM,
> dashboard client, API key, retry, or duplicate event can bypass approval. Do not
> activate an external submission endpoint without a separate written approval.

## Prompt 10 — Model evaluation, release, and monitoring

> Add a governed release process for ChargeGuard's deterministic win-probability
> model. Keep model training separate from serving; record feature schema,
> training-data provenance, real/synthetic split, metrics by network and reason,
> calibration, decision threshold, approval, and rollback target. Prevent model
> promotion when data quality, calibration, fairness, or minimum sample checks
> fail. Add post-release drift and outcome monitoring. Advisory LLMs remain
> read-only and cannot affect probability, expected value, decision, filing, or
> outcome. Test failed validation, incompatible feature schema, rollback, and
> degraded model loading.

## Prompt 11 — Privacy, retention, and access governance

> Add a production privacy and data-governance layer for merchant, customer, and
> evidence data. Define data classification, retention periods, legal holds,
> deletion/anonymization flows, export controls, and access-review reports. Make
> destructive retention jobs dry-run first and require a documented approval path.
> Retain only the minimum data needed for dispute handling and auditability. Add
> tests for tenant-scoped export, redaction, expiry selection, legal-hold exclusion,
> and audit logging. Do not delete real data or make legal claims without review.

## Prompt 12 — Production launch readiness and disaster recovery

> Build a production launch checklist and disaster-recovery plan for ChargeGuard.
> Cover infrastructure-as-code or reproducible configuration, database backups and
> restore drills, object storage recovery, secret rotation, job recovery, Supabase
> outage behavior, alert ownership, SLOs, incident severity, rollback, and a
> staging-to-production promotion gate. Add automated readiness checks that fail
> closed for missing critical configuration. Exercise recovery only with disposable
> or explicitly approved staging resources. Report the remaining external
> prerequisites separately from code that is complete.

## Recommended order from today

1. Prompt 3: select and implement a managed secret backend.
2. Prompt 4: immutable private artifacts.
3. Prompt 5: durable outbox/jobs.
4. Prompt 6 and Prompt 7: operations plus financial/evidence correctness.
5. Prompt 8: Razorpay Test Mode certification.
6. Prompt 9 through Prompt 12: approval, model governance, privacy, and launch.

The product remains a demo/staging system until those steps, the external
provisioning they require, and a human-reviewed pilot are complete.
