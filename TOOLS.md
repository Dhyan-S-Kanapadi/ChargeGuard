# ChargeGuard toolchain and local setup

This is the inventory of tools and services already used by ChargeGuard. Reuse
them before adding a dependency or a new service.

## Application stack

| Area | Tools already used | Where to look |
| --- | --- | --- |
| Backend | Python 3.11, FastAPI, Pydantic, Uvicorn | `main.py`, `api/` |
| Workflow | LangGraph and the shared `ChargebackState` | `core/graph.py`, `core/state.py` |
| ML | scikit-learn logistic regression; deterministic synthetic seed data | `ml/` |
| PDFs | ReportLab | `documents/pdf_builder.py` |
| HTTP | httpx | `integrations/` |
| Database | PostgreSQL through psycopg; versioned SQL migrations | `db/` |
| Local persistence | synchronized JSON application store | `api/local_store.py` |
| Secret storage (current pilot) | Fernet-encrypted file adapter | `integrations/credential_secrets.py` |
| Identity | Supabase-compatible JWT verification with PyJWT | `api/identity.py`, `db/identity.py` |
| Frontend | React, TypeScript, Vite, TanStack Query, Zod | `frontend/` |
| Tests | pytest, pytest-asyncio, Vitest, Testing Library, MSW, Playwright | `tests/`, `frontend/src/**/*.test.*` |
| Containers and CI | Docker, Docker Compose, GitHub Actions | `Dockerfile`, `docker-compose.yml`, `.github/workflows/ci.yml` |

## Integrated providers and boundaries

| Purpose | Implementation | Important boundary |
| --- | --- | --- |
| Payments | Razorpay primary; Stripe secondary | Live calls require a verified merchant-owned connector. |
| Shipping | Shiprocket, Delhivery | Failure becomes neutral/degraded evidence, never fabricated evidence. |
| Support | Freshdesk, Gmail | Structured retrieval; an LLM is not required to fetch records. |
| Device risk | SEON | Only normalized evidence is retained. |
| Consortium | Ethoca, Verifi | Provider failures degrade safely. |
| Food delivery | local adapter, optional Claude Vision | Vision interprets evidence only; it cannot decide a case. |
| Advisory AI | OpenAI-compatible decision review and portfolio assistant | Read-only/advisory; cannot control authoritative fields. |

Razorpay webhooks use exact-body HMAC-SHA256. Do not replace this with API-key
authentication, parsed-body signing, or a bypass for tests.

## Required local tools

- Git
- Python 3.11 and Poetry
- Node.js 22 and npm
- Docker Desktop and Docker Compose for container validation
- PostgreSQL 16 only when running the database integration suite

Install project dependencies from the repository root:

```powershell
poetry install
cd frontend
npm ci
cd ..
```

## Common commands

| Goal | Command |
| --- | --- |
| Run backend tests | `poetry run pytest -q` |
| Run one backend test file | `poetry run pytest -q tests/test_razorpay_webhooks.py` |
| Run frontend tests | `cd frontend; npm test` |
| Lint/type-check/build frontend | `cd frontend; npm run lint; npm run typecheck; npm run build` |
| Train deterministic baseline model | `poetry run python -m ml.train` |
| Start API locally | `poetry run python -m uvicorn main:app --port 8000` |
| Start containerized local environment | `docker compose --env-file .env.local up --build` |
| Run simulator catalog against local API | `py scripts/run_simulation_catalog.py --base-url http://127.0.0.1:8200 --merchant merchant_demo` |
| Validate uncommitted diff | `git diff --check` |
| Inspect current changes | `git status --short` |

The simulator is loopback-only, signed, and disabled in production. It does not
contact Razorpay or create a real dispute.

## Configuration safety

- Keep secrets in deployment secret settings or ignored local files; never in
  source, screenshots, commit history, or prompts.
- `CHARGEGUARD_USE_STUBS=true` is the normal local/demo default. A provider
  override such as `RAZORPAY_USE_STUBS=false` is for a deliberately configured,
  isolated staging exercise—not proof of a production integration.
- The reviewer demo uses synthetic evidence and local filing. Its optional Groq
  keys are server-side only.
- The current JSON/credential-file approach is single-process pilot
  infrastructure. PostgreSQL is available for staged use; managed secrets,
  immutable artifacts, and durable jobs are still roadmap items.

For the full environment matrix and provider-specific variables, use
[CLAUDE.md](CLAUDE.md) and [RAZORPAY_TEST_MODE_CONNECTIVITY.md](RAZORPAY_TEST_MODE_CONNECTIVITY.md).
