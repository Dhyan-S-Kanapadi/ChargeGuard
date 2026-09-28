# ChargeGuard roadmap and remaining work

## Current status

ChargeGuard is a capable local/staging prototype and reviewer demo. It is **not
approved for real chargeback handling or autonomous production use**. The filing
agent creates a local confirmation; it does not accept, contest, upload, or
submit a real provider/card-network response.

Implemented foundations include the LangGraph workflow, signed Razorpay-shaped
simulator, deterministic decisioning, evidence integrations with safe stub
fallbacks, local rebuttal generation/quality checks, filed-only learning,
merchant-scoped payment and SEON connectors, PostgreSQL store migration,
Supabase-compatible identity/authorization, dashboard, and advisory LLM review.

## Delivery order

Work through these stages in order. Each stage requires focused tests, the full
suite, `git diff --check`, review, and a commit before the next stage begins.

| Stage | Remaining outcome | Key prerequisite / guardrail |
| --- | --- | --- |
| 3 | Managed connector-secret backend | Select the existing cloud vault with the operator first; preserve the `CredentialSecretStore` contract and fail closed. |
| 4 | Private immutable evidence and rebuttal artifacts | Tenant/case-scoped objects, checksums, immutable metadata, and authorized short-lived access. |
| 5 | Durable webhook-to-workflow jobs | PostgreSQL outbox/worker, transactional enqueue, leases, retries, recovery, and dead-letter handling. Do not add a queue service unless PostgreSQL demonstrably cannot satisfy the need. |
| 6 | Production observability and remediation | Redacted structured logs, metrics, correlation IDs, alerts, and operator runbooks. |
| 7 | Financial, deadline, and evidence correctness | Versioned/attributable FX and economics, precision rules, evidence provenance/integrity, and fail-safe handling of gaps. |
| 8 | Controlled Razorpay Test Mode certification | Isolated HTTPS staging only; prove signing, duplicates, ordering, recovery, reconciliation, and safe outcomes without live submission. |
| 9 | Human approval and manual submission boundary | Two-person-review-ready audit trail, immutable approved artifacts, valid deadline/evidence/playbook, and disabled-by-default submission adapter. |
| 10 | Model evaluation, release, and monitoring | Data provenance, calibration and release gates, rollback, drift/outcome monitoring. |
| 11 | Privacy, retention, and access governance | Classification, retention/legal holds, deletion/anonymization, exports, and access reviews. |
| 12 | Launch readiness and disaster recovery | Reproducible configuration, backup/restore drills, incident ownership, SLOs, rollback, and a staging-to-production gate. |

The detailed implementation prompts and acceptance constraints live in
[PRODUCTION_IMPLEMENTATION_PROMPTS.md](PRODUCTION_IMPLEMENTATION_PROMPTS.md).
The current production assessment lives in
[PRODUCTION_IMPLEMENTATION.md](PRODUCTION_IMPLEMENTATION.md).

## Product work after the safety foundation

- Replace synthetic/provider-stub evidence with independently validated
  merchant integrations, one provider at a time.
- Complete live food and quick-commerce platform adapters.
- Add a separate UPI refund/reversal workflow with deterministic RBI/NPCI SLA
  rules. Do not present UPI as a card chargeback or RuPay automatically.
- Add real, human-approved card-network filing adapters only after stages 4–9.
- Keep logistic regression until enough real filed outcomes support a governed
  model evaluation; do not promote an alternative model based on synthetic data.

## Never treat these as complete

- A simulated webhook is not a Razorpay Test Mode or production certification.
- A provider connector verification is not evidence that an account has a
  retrievable dispute or supports an action.
- A local `filed_*` confirmation is not external submission.
- A passing test suite is not a production launch approval.
- An LLM recommendation is not a financial or filing decision.

## How to choose the next task

1. Read this file, [AGENTS.md](AGENTS.md), and the matching production prompt.
2. Confirm the prerequisite stage is complete and committed.
3. Inspect the state contract, implementation, and relevant tests.
4. Obtain explicit approval before cloud provisioning, real provider calls,
   database migrations outside an isolated test environment, deployment, or any
   external submission.
