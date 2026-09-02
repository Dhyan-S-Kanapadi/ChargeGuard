# ChargeGuard Engineering Rules

Before performing substantial work in this repository, read the root-level `CLAUDE.md`. Treat `CLAUDE.md` as the primary project-context document for ChargeGuard. Then inspect the relevant implementation and tests before making changes.

ChargeGuard is a financial dispute and chargeback management system. Keep responsibilities separated: `CLAUDE.md` owns project knowledge and architectural context; this `AGENTS.md` owns Codex operating rules, engineering constraints, safety requirements, testing expectations, and development practices. The executable code, schemas, and tests remain authoritative when documentation and behavior differ.

## Priority Order

Use this order for engineering decisions:

1. Correctness
2. Security
3. Data integrity
4. Reliability
5. Auditability
6. Maintainability
7. Simplicity
8. Performance, unless performance is a demonstrated requirement

Prefer simplicity, but never at the expense of financial correctness, security, reliability, data integrity, or auditability.

## Never Simplify Away

Never remove or weaken these controls merely to reduce code:

- Webhook signature verification
- Authentication and authorization
- API-key validation
- Input validation
- Idempotency, replay protection, and duplicate-event protection
- Webhook retry handling and event ordering protection
- Financial and dispute state validation
- Database transaction safety
- Audit logging and important observability
- Evidence integrity and provider verification
- Timeout and error handling
- Rate limiting
- Tests protecting critical workflows

## Webhook Rules

Webhook endpoints are trust boundaries. Every implementation must account for signature verification before payload trust, malformed payloads, unknown event types, duplicate delivery, retries, replay, out-of-order events, provider timeouts, database failures, partial processing, safe retries, deterministic state transitions, and logging without secrets. Never assume exactly-once delivery.

For Razorpay, verify HMAC over the exact raw body before parsing. Preserve event idempotency, PII-minimized persistence, fast acknowledgement, recoverable background processing, and stale-state protection.

## Financial State Rules

Changes involving disputes, chargebacks, payments, refunds, evidence, deadlines, webhook events, merchants, providers, or transactions must preserve valid deterministic state transitions.

Do not silently regress state or overwrite stronger state with stale provider events. Keep a ChargeGuard decision distinct from a network-adjudicated outcome: `FIGHT` is not `WIN`, `ACCEPT` is not `LOSS`, and only a filed case may receive a real `WIN` or `LOSS` training label.

## Security Rules

Never expose Razorpay key secrets, API keys, webhook secrets, OAuth tokens, database credentials, internal tokens, customer-sensitive data, or raw secrets in logs.

- Never commit `.env` or generated payloads containing secrets or PII.
- Use environment variables or approved secret storage for credentials.
- Do not weaken authentication for convenience.
- Do not disable signature verification to make tests pass.
- Keep API redaction and internal-token gates intact.

## Architecture Rules

Prefer existing project patterns. Before creating an abstraction, determine whether the current architecture already solves the requirement.

Do not introduce unnecessary microservices, repositories, factories, managers, service layers, wrapper classes, strategy classes, queues, caches, databases, infrastructure, agent layers, LLM calls, MCP servers, or background workers without a concrete current requirement.

Avoid hypothetical future-proofing. Use the simplest architecture that safely meets the current requirement.

## Dependency Rules

Before adding a dependency:

1. Check whether the Python standard library can safely solve the problem.
2. Check whether FastAPI, Pydantic, scikit-learn, or another existing dependency already solves it.
3. Add a package only when it provides meaningful value.

Do not add packages merely to wrap simple existing functionality.

## AI And Agent Rules

Prefer deterministic code for authentication, signature validation, financial calculations, deadlines, state transitions, API validation, provider event routing, permissions, and security decisions.

LLMs may assist with evidence interpretation, document classification, summaries, drafting, and explanations. Treat their output as untrusted and surround it with deterministic safeguards. LLM output must not independently alter win probability, expected value, filing eligibility, or case decision.

## Testing Requirements

Critical workflow changes should include relevant coverage for the successful path and applicable invalid input, duplicate events, malformed events, invalid authentication, invalid signatures, retries, unknown events, stale events, unexpected event order, database failures, idempotent reprocessing, and edge cases.

Do not delete tests because they make simplification difficult. Do not consider a critical change complete until focused tests and the full suite pass, or a concrete external blocker is reported.

The standard command is:

```powershell
poetry run pytest -q
```

If Poetry is unavailable but dependencies are installed, use:

```powershell
py -m pytest -q
```

Provider tests must use mocks, stubs, or the local simulator and must not make unapproved network calls or require real credentials.

## Refactoring Rules

Refactoring must preserve observable behavior unless the task explicitly changes it. Before deleting code, identify why it exists, search callers and tests, inspect Git history when useful, and determine whether it protects an edge case.

Do not delete defensive logic based on a superficial unused-code search. Keep changes scoped and preserve unrelated work in a dirty worktree.

## Coding Style

Prefer clear names, small understandable functions, direct control flow, explicit validation, existing reusable helpers, and the minimum necessary abstraction. Avoid cleverness. Code must be easy for another engineer to audit.

## Change Discipline

- Inspect `git status` before editing and preserve all user changes.
- Do not modify `.env` or expose its values.
- Do not commit generated PDFs, model artifacts, outcome data, caches, or provider payloads.
- Do not change authentication, Razorpay verification, filing, or financial semantics outside the explicit task.
- Run focused tests after behavioral changes and the full suite before a requested commit.
- Commit and push only when explicitly requested.
- Never merge into `main` or `develop` without explicit authorization.
