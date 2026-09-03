# ChargeGuard Ponytail Policy

Ponytail favors the smallest implementation that safely meets the current requirement. In ChargeGuard, correctness and security always take precedence over reducing code.

## Availability

Before relying on Ponytail, check whether it is installed and configured.

- If it is available, use it conservatively under the rules below.
- If it is unavailable or not fully configured, continue under `AGENTS.md`; do not block unrelated work.

## Intensity

- Use `lite` for architecture, Razorpay integrations, webhooks, database-state changes, authentication, and other security-sensitive work.
- Use `full` only for ordinary, low-risk implementation or refactoring when appropriate.
- Never use `ultra` unless the user explicitly requests it.

## ChargeGuard Overrides

Never accept or propose a Ponytail simplification that weakens:

- correctness or financial-state integrity
- security, authentication, authorization, or API-key validation
- Razorpay webhook signature verification
- payload validation
- idempotency, replay protection, or duplicate-event protection
- webhook retries, recovery, or event-ordering protection
- dispute-state or outcome-eligibility validation
- database transaction safety or persistence integrity
- evidence integrity or redaction
- audit logging or defensive error handling
- tests protecting critical workflows

Use deterministic code—not LLM reasoning—for authentication, signatures, permissions, financial calculations, deadlines, state transitions, provider routing, webhook validation, and security decisions.

## Implementation Rules

- Understand the requested behavior and inspect the relevant implementation and tests before simplifying.
- Prefer existing project patterns and dependencies.
- Reuse existing functionality where appropriate.
- Make the smallest safe change that fully solves the requirement.
- Do not future-proof for hypothetical requirements.
- Do not add unnecessary abstractions, services, repositories, factories, wrappers, queues, caches, databases, workers, agents, dependencies, or LLM calls.
- Preserve observable behavior unless the task explicitly changes it.
- Add or update relevant tests and run them after behavioral changes.

When this file conflicts with `AGENTS.md`, `AGENTS.md` controls. Executable code, schemas, and tests remain authoritative for current system behavior.
