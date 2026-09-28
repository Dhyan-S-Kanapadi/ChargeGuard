# Contributing to ChargeGuard

This guide is the starting point for a new human contributor or coding agent.
It describes the current repository; executable code, schemas, and tests remain
authoritative when a document disagrees with them.

## Start here

1. Clone the repository and enter its root:

   ```powershell
   git clone https://github.com/Dhyan-S-Kanapadi/ChargeGuard.git
   cd ChargeGuard
   ```

2. Read [AGENTS.md](AGENTS.md), then [CLAUDE.md](CLAUDE.md). They define the
   engineering constraints and the current architecture.
3. Read [TOOLS.md](TOOLS.md) for local setup and the tools already in use.
4. Read [ROADMAP.md](ROADMAP.md) before selecting new work. Do not start a
   later production stage before its prerequisite is reviewed and committed.
5. Check `git status --short` before editing. Preserve unrelated work.

## Project at a glance

ChargeGuard is an India-first chargeback investigation and dispute-preparation
system. FastAPI accepts normalized internal chargebacks and signed Razorpay
events. A LangGraph workflow collects evidence, calculates deterministic win
probability and expected value, then chooses `FIGHT`, `ACCEPT`, or
`ESCALATE_DEGRADED`.

`FIGHT` builds and quality-checks a rebuttal and records a **local filing
confirmation only**. It does not submit to Razorpay or a card network. `WIN`
and `LOSS` are allowed only for filed cases and are the only outcomes eligible
for learning.

The shared contract is `core/state.py::ChargebackState`; the executable graph is
`core/graph.py::app`. Start every feature by tracing the affected state fields,
graph nodes, API route, and tests.

## Safety invariants

- Verify Razorpay HMAC over the exact raw request bytes before parsing payloads.
- Keep webhook processing idempotent, recoverable, ordered, and PII-minimized.
- Never infer a card network or network reason code from an ambiguous provider
  value. UPI is not RuPay.
- Keep `FIGHT` distinct from `WIN` and `ACCEPT` distinct from `LOSS`.
- Use deterministic code for authentication, authorization, money, deadlines,
  state changes, webhook validation, and filing eligibility.
- Treat every LLM response as advisory and untrusted. It cannot change the
  decision, probability, expected value, routing, filing, or outcome.
- Do not read, print, commit, or modify `.env` files or real credentials.

## Development workflow

Use Python 3.11 for the supported runtime. Install backend dependencies with
Poetry and frontend dependencies with npm:

```powershell
poetry install
cd frontend
npm ci
cd ..
```

For local simulated development, set only safe values in an ignored local
environment file or shell environment. Keep provider stubs enabled and use the
local Razorpay simulator; provider tests must not make unapproved real calls.
See [TOOLS.md](TOOLS.md) and [SIMULATION_TEST_MATRIX.md](SIMULATION_TEST_MATRIX.md).

Before proposing a commit:

```powershell
poetry run pytest -q
cd frontend
npm run lint
npm run typecheck
npm test
npm run build
cd ..
git diff --check
```

On Windows, if pytest cannot create its default temporary files, use a dedicated
writable workspace directory such as:

```powershell
py -m pytest -q -p no:cacheprovider --basetemp D:\ChargeGuard\pytest-local
```

Do not commit that temporary directory, generated PDFs, model artifacts, test
payloads, caches, or `.env` files.

## Change rules

- Make the smallest safe change. Reuse existing patterns and dependencies.
- For Razorpay, authentication, persistence, financial state, or other
  sensitive work, apply Ponytail in `lite` mode: simplify only after tracing
  the complete flow and never remove a protection.
- Add focused tests for behavioral changes; critical webhook and financial
  changes also need invalid-input, duplicate, retry, ordering, and failure-path
  coverage as applicable.
- Do not commit, push, deploy, migrate a real database, contact a live provider,
  or enable submission without explicit authorization.

## Documentation map

| Need | Read |
| --- | --- |
| Engineering rules and coding-agent behavior | [AGENTS.md](AGENTS.md) |
| Architecture, contracts, API, and deployment boundaries | [CLAUDE.md](CLAUDE.md) |
| Toolchain, commands, and provider boundaries | [TOOLS.md](TOOLS.md) |
| Planned work and production gaps | [ROADMAP.md](ROADMAP.md) |
| End-to-end flow | [CHARGEGUARD_COMPLETE_FLOWCHART.md](CHARGEGUARD_COMPLETE_FLOWCHART.md) |
| Demo/reviewer walkthrough | [HACKATHON_DEMO.md](HACKATHON_DEMO.md), [REVIEWER_DEMO.md](REVIEWER_DEMO.md) |
| Razorpay Test Mode plan | [RAZORPAY_TEST_MODE_CONNECTIVITY.md](RAZORPAY_TEST_MODE_CONNECTIVITY.md) |
| Supabase identity rollout | [SUPABASE_AUTH_SETUP.md](SUPABASE_AUTH_SETUP.md) |
| PostgreSQL store constraints | [db/README.md](db/README.md) |

`context.md` and `branches.md` are historical notes, not planning sources.
