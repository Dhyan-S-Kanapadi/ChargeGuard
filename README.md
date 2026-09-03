# ChargeGuard AI

Autonomous chargeback dispute management for Indian merchants.

ChargeGuard AI investigates incoming chargebacks, gathers evidence from payment, logistics, support, fraud, and consortium systems, scores whether the dispute is worth fighting, generates a deterministic rebuttal PDF, files the response through a stubbed filing layer, and records final outcomes for model retraining.

The product is designed around a success-fee business model, so the core decision is financial: fight only when the expected value is positive.

## What It Does

When a chargeback webhook is received:

1. The FastAPI webhook validates the payload and creates a `ChargebackState`.
2. The LangGraph workflow routes the case through evidence agents.
3. Evidence agents collect transaction, shipping, communications, device, consortium, and food or quick-commerce evidence when relevant.
4. The scoring agent predicts win probability and computes expected value.
5. If the decision is `ACCEPT`, the case is logged and no rebuttal is generated.
6. If the decision is `FIGHT`, the system builds a rebuttal PDF.
7. The quality agent validates required evidence, formatting, factual consistency, page limits, and prohibited language.
8. Approved PDFs are filed through the local filing stub.
9. Final `WIN` or `LOSS` outcomes are appended to the learning dataset and can trigger retraining.

## Current Implementation Status

Implemented:

- FastAPI application entrypoint and health check
- Merchant registration, listing, and retrieval endpoints
- React and TypeScript operator dashboard served at `/dashboard/`
- Chargeback webhook endpoint
- In-memory dispute store for local development, with optional JSON persistence
- LangGraph workflow with all core nodes wired
- Orchestrator playbook routing
- Razorpay and Stripe support (adapter pattern designed to extend to additional gateways)
- Shipping evidence with Shiprocket and Delhivery support
- Communications evidence with Freshdesk and Gmail reader support
- Device evidence with SEON support
- Consortium evidence with Ethoca and Verifi support
- Food and quick-commerce evidence stubs
- ML feature extraction
- Logistic regression win-probability model
- Expected value based `FIGHT` or `ACCEPT` decisioning
- Deterministic ReportLab PDF generation
- Rebuttal templates and playbooks for selected Visa and Mastercard reason codes
- Quality check loop with a hard limit of 3 attempts
- Filing stub that writes local filing output
- Outcome recording endpoint
- Learning feedback dataset and retraining threshold
- Unit and integration tests for the major components

Not yet production-ready:

- Card-network portal submission is still a local stub.
- Food and quick-commerce platform integrations are currently stub-first.
- JSON API persistence is available for local durability; production still needs database-backed storage.
- Provider credentials and merchant configuration need secure storage.
- Live provider API behavior still depends on merchant-specific contracts and sandbox access.

## Tech Stack

- Python 3.11
- Node.js 22 (frontend build and development)
- FastAPI
- LangGraph
- Anthropic Claude API for deterministic language and vision tasks
- LangSmith tracing hooks
- ReportLab for PDF generation
- scikit-learn baseline ML model
- httpx for provider integrations
- pytest and pytest-asyncio
- React, TypeScript, Vite, TanStack Query, Zod, Recharts, and Lucide
- Vitest, React Testing Library, MSW, and Playwright
- Docker Compose for the single ChargeGuard API service

## Repository Layout

```text
chargeguard/
├── main.py
├── pyproject.toml
├── docker-compose.yml
├── core/
│   ├── state.py
│   ├── graph.py
│   ├── config.py
│   └── enums.py
├── agents/
│   ├── orchestrator.py
│   ├── scoring.py
│   ├── rebuttal_builder.py
│   ├── quality_check.py
│   ├── filing.py
│   ├── learning.py
│   └── evidence/
├── integrations/
├── ml/
├── documents/
├── api/
├── db/
└── tests/
```

## Core Design

### Single Source Of Truth

All workflow data flows through `core.state.ChargebackState`. Agents read the shared state and write only the field they own.

Important state sections:

- Input chargeback metadata
- Merchant profile
- Evidence objects
- ML decision fields
- Rebuttal and filing fields
- Final outcome and learning fields

### LangGraph Workflow

The compiled graph is exposed from `core.graph.app`.

Current path:

```text
orchestrator
  -> transaction_evidence
  -> shipping_evidence
  -> device_evidence
  -> comms_evidence
  -> consortium_evidence
  -> optional food evidence
  -> scoring
  -> ACCEPT: accept_and_log
  -> FIGHT: rebuttal_builder -> quality_check -> filing
  -> optional learning when final outcome is WIN or LOSS
```

The quality path can loop back to `rebuttal_builder`, but it stops after 3 failed quality checks and escalates to human review.

### Decision Rule

The scoring agent is deterministic:

```text
expected_value = (win_probability * dispute_amount) - response_cost
decision = FIGHT if expected_value > threshold else ACCEPT
```

Defaults:

- `RESPONSE_COST_USD=15.0`
- `FIGHT_EV_THRESHOLD=0.0`

### LLM Boundaries

ChargeGuard has four additive LLM call sites: delivery photo verification, case summary, rebuttal narrative, and portfolio assistant. None influence `win_probability`, `expected_value`, or `decision`; those fields remain deterministic, auditable, and computed without LLM involvement.

## Getting Started

### Prerequisites

- Python 3.11
- Node.js 22
- Poetry
- Docker, optional
- Provider sandbox credentials, optional

### Install

```bash
poetry install
cd frontend
npm ci
cd ..
```

### Configure Environment

Create a local `.env` file from the example:

```bash
cp .env.example .env
```

For local deterministic development, keep:

```env
CHARGEGUARD_USE_STUBS=true
```

This allows the evidence agents to run without live provider credentials.

### Train Or Generate The Baseline Model

The scoring agent expects a model artifact at:

```text
./ml/artifacts/win_probability_model.pkl
```

Run the training script if the artifact is missing:

```bash
poetry run python -m ml.train
```

### Run The API

```bash
poetry run uvicorn main:app --reload --port 8000
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

Expected response:

```json
{"status":"ok","model_loaded":true,"stub_mode":true}
```

### Run The Frontend

Run FastAPI on port 8000, then start Vite in a second terminal:

```bash
cd frontend
npm run dev
```

Vite proxies ChargeGuard API paths to `http://127.0.0.1:8000`. Enter the API origin and a local `API_KEY` on the connection screen. The key stays in memory by default; the explicit tab-only option uses `sessionStorage`, never `localStorage`.

Build the production dashboard with:

```bash
cd frontend
npm run build
```

FastAPI serves `frontend/dist` at `/dashboard/` when present and falls back to the legacy `static/` dashboard when the React build is absent. Hash routing keeps dashboard routes reliable beneath the base path.

## Demo

Train the model, start the API with deterministic stub evidence and a local API key, then run the manual demo:

```bash
poetry run python -m ml.train
CHARGEGUARD_USE_STUBS=true API_KEY=demo-key poetry run uvicorn main:app --port 8000
API_KEY=demo-key poetry run python scripts/demo.py
```

The script creates one merchant and demonstrates the FIGHT, ACCEPT, and ESCALATE_DEGRADED decision paths, including the generated FIGHT rebuttal PDF path.

## Testing

1. Run the full automated suite:

   ```bash
   poetry run pytest -q
   ```

2. Run the manual API decision walkthrough for FIGHT, ACCEPT, and ESCALATE_DEGRADED. It requires a running server:

   ```bash
   bash scripts/demo.sh
   ```

3. Verify that real outcomes accumulate and trigger retraining. It also requires a running server; set `RETRAIN_RECORD_THRESHOLD=3` before starting it for a fast run:

   ```bash
   bash scripts/test_feedback_loop.sh
   ```

4. Run the model validity checks without a server. This performs synthetic cross-validation and feature ablation:

   ```bash
   poetry run python scripts/model_sanity_check.py
   ```

These checks confirm that the system is internally consistent and behaves sensibly. They do not establish accuracy against real-world fraud or chargeback outcomes; that requires real production filed-dispute data.

## API Usage

### Register A Merchant

```bash
curl -X POST http://127.0.0.1:8001/merchants \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "merchant_id": "merchant_demo",
    "name": "Demo Store",
    "vertical": "ecommerce",
    "payment_provider": "razorpay",
    "shipping_provider": "shiprocket",
    "average_order_value": 1200,
    "chargeback_history_count": 4
  }'
```

### Submit A Chargeback Webhook

Use a future `filing_deadline`.

```bash
curl -X POST http://127.0.0.1:8001/webhook/chargeback \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "chargeback_id": "cb_demo_001",
    "reason_code": "13.1",
    "card_network": "VISA",
    "dispute_amount": 1499,
    "currency": "INR",
    "filing_deadline": "2026-07-15T00:00:00Z",
    "merchant_id": "merchant_demo",
    "order_id": "order_demo_001",
    "payment_id": "pay_demo_001",
    "tracking_id": "trk_demo_001"
  }'
```

### List Disputes

```bash
curl http://127.0.0.1:8001/disputes -H "X-API-Key: $API_KEY"
```

### Get A Dispute

```bash
curl http://127.0.0.1:8001/disputes/cb_demo_001 -H "X-API-Key: $API_KEY"
```

### Record Final Outcome

```bash
curl -X POST http://127.0.0.1:8001/disputes/cb_demo_001/outcome \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "outcome": "WIN",
    "reason": "Issuer accepted proof of delivery and authentication evidence."
  }'
```

## Environment Variables

Core:

| Variable | Purpose |
| --- | --- |
| `CHARGEGUARD_USE_STUBS` | Uses deterministic stub evidence when `true`. |
| `CHARGEGUARD_STORE_PATH` | Optional JSON file path for local merchant and dispute persistence. |
| `ANTHROPIC_API_KEY` | Claude API key for rebuttal and vision tasks. |
| `LANGSMITH_API_KEY` | LangSmith tracing key. |
| `LANGSMITH_PROJECT` | LangSmith project name. |
| `REBUTTAL_OUTPUT_DIR` | Directory for generated rebuttal PDFs. |

ML:

| Variable | Purpose |
| --- | --- |
| `MODEL_PATH` | Path to the win-probability model artifact. |
| `RESPONSE_COST_USD` | Cost used in expected-value calculation. |
| `FIGHT_EV_THRESHOLD` | Minimum EV threshold for fighting a case. |
| `TRAINING_DATA_PATH` | Feedback dataset path. |
| `TRAINING_METADATA_PATH` | Retraining metadata path. |
| `PLAYBOOK_STATS_PATH` | Playbook outcome stats path. |
| `RETRAIN_RECORD_THRESHOLD` | Number of new outcomes required before retraining. |

Providers:

| Variable | Purpose |
| --- | --- |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | Razorpay payment evidence. |
| `STRIPE_API_KEY` | Stripe payment evidence. |
| `SHIPROCKET_EMAIL` / `SHIPROCKET_PASSWORD` | Shiprocket shipment evidence. |
| `DELHIVERY_API_TOKEN` | Delhivery fallback shipment evidence. |
| `FRESHDESK_API_KEY` / `FRESHDESK_DOMAIN` | Support ticket evidence. |
| `GMAIL_ACCESS_TOKEN` / `GMAIL_USER_ID` | Gmail thread evidence. |
| `SEON_API_KEY` | Device and fraud evidence. |
| `ETHOCA_API_KEY` / `ETHOCA_BASE_URL` | Ethoca consortium checks. |
| `VERIFI_API_KEY` / `VERIFI_BASE_URL` | Verifi consortium checks. |

## Testing

Run the full suite:

```bash
poetry run pytest -q
```

Or without Poetry if the environment is already active:

```bash
python -m pytest -q
```

Focused examples:

```bash
python -m pytest -q tests/test_graph.py
python -m pytest -q tests/test_api.py
python -m pytest -q tests/test_scoring.py
```

Frontend checks:

```bash
cd frontend
npm run lint
npm run typecheck
npm test
npm run build
npx playwright test
```

## Dashboard Deployment

The Dockerfile uses a Node 22 build stage and `npm ci` to compile the dashboard, then copies only `frontend/dist` into the Python 3.11 runtime image. No API key or provider secret is a frontend build argument. The container respects `PORT`, so the same image is compatible with Render's Docker runtime. Configure secrets only as server-side environment variables and keep one application process while using the synchronized JSON store.

## Development Notes

- Keep all Claude API calls deterministic with `temperature=0`.
- Do not allow the quality loop to exceed 3 attempts.
- Do not let the orchestrator fetch evidence; it only plans and routes.
- External API failures should return empty or fallback evidence instead of crashing the graph.
- PDF generation must be deterministic for the same input.
- The learning agent should only persist feedback after `final_outcome` is `WIN` or `LOSS`.
- Keep `ChargebackState` as the workflow contract between agents.

## Provider Fallback Strategy

Evidence agents are built to prefer the configured merchant provider and fall back where supported:

- Payments: Razorpay or Stripe.
- Shipping: Shiprocket, with Delhivery fallback.
- Communications: Freshdesk and Gmail can both contribute evidence.
- Fraud/device: SEON.
- Consortium: Ethoca and Verifi.

When `CHARGEGUARD_USE_STUBS=true`, deterministic evidence is returned for local development and repeatable tests.

## Testing Against Real Providers

`CHARGEGUARD_USE_STUBS` is the global default: set it to `true` to use stubs everywhere. To test one provider without changing the others, set that provider's `*_USE_STUBS=false` override and provide its real credentials; unset overrides continue to follow the global setting. Start with Razorpay sandbox test-mode keys from the Razorpay dashboard, which do not move real money, before attempting a live run across multiple providers.

For multiple merchants, set a non-secret `support_connector_ref` such as `ACME` when creating the merchant. ChargeGuard first reads `CHARGEGUARD_CONNECTOR_ACME_GMAIL_ACCESS_TOKEN`, `CHARGEGUARD_CONNECTOR_ACME_GMAIL_USER_ID`, `CHARGEGUARD_CONNECTOR_ACME_FRESHDESK_API_KEY`, and `CHARGEGUARD_CONNECTOR_ACME_FRESHDESK_DOMAIN`, then falls back to the global variables above. Merchant fields `gmail_user_id` and `freshdesk_domain` override their corresponding environment values; credentials remain outside API payloads and responses.

## Razorpay Dispute Webhooks

Use dispute webhooks for primary real-time detection and the direct Razorpay REST API for reconciliation and operator-approved dispute actions. Razorpay MCP is not part of the ingestion path because its documented tools do not expose dispute webhook ingestion.

Configure the Razorpay Dashboard webhook callback as:

```text
https://<your-public-host>/webhook/razorpay
```

Enable all six events: `payment.dispute.created`, `payment.dispute.action_required`, `payment.dispute.under_review`, `payment.dispute.won`, `payment.dispute.lost`, and `payment.dispute.closed`. Set the same webhook secret in the Dashboard and `RAZORPAY_WEBHOOK_SECRET`. The webhook secret is different from `RAZORPAY_KEY_SECRET`, which authenticates outgoing REST requests. Test/live behavior is selected by the Razorpay key pair (`rzp_test_...` versus live keys); use Test mode for staging.

The endpoint has no `X-API-Key` dependency because Razorpay authenticates with `X-Razorpay-Signature`. ChargeGuard verifies HMAC-SHA256 over the exact raw body before parsing, limits body size, uses `x-razorpay-event-id` for idempotency, and falls back to a deterministic payload hash when that header is absent. It stores the payload hash plus an allowlisted, PII-minimized event projection rather than the full webhook body. Unknown merchant accounts are acknowledged and recorded as `unresolved` by deferred processing for protected inspection at `GET /internal/razorpay/events?processing_state=unresolved`.

Map a Razorpay account to a merchant before enabling delivery:

```bash
curl -X POST https://<your-public-host>/merchants \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"merchant_id":"merchant_001","name":"Example Merchant","vertical":"ecommerce","payment_provider":"razorpay","razorpay_account_id":"acc_...","freshdesk_domain":"","average_order_value":2500,"chargeback_history_count":0}'
```

Provider `reason_code` remains a Razorpay reason and is never translated into a Visa, Mastercard, or RuPay reason code. Card network is used only when present in an expanded card object or obtained through `GET /v1/payments/:id?expand[]=card`. UPI is recorded as `payment_rail=UPI` with no card network; it is never treated as RuPay. Cases without a reliable card network and supported network playbook are ingested and routed to human review.

### Reconciliation

Webhooks are primary. A protected reconciliation endpoint catches missed events and status drift through `GET /v1/disputes` and the same normalization/upsert path:

```bash
curl -X POST http://127.0.0.1:8000/internal/razorpay/reconcile \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"merchant_id":"merchant_001","count":100}'
```

`RazorpayClient` also provides tested fetch, accept, contest, expanded-payment, and dispute-evidence document methods using Razorpay Basic Authentication. Accept and contest are irreversible operator actions and are not invoked by the simulator.

### Local Razorpay Simulator

Set `ENVIRONMENT=development`, `RAZORPAY_SIMULATOR_ENABLED=true`, `RAZORPAY_WEBHOOK_SECRET`, and `API_KEY`, then start ChargeGuard:

```bash
py -m uvicorn main:app --port 8000
py scripts/simulate_razorpay_dispute.py card-created
py scripts/simulate_razorpay_dispute.py upi-created
py scripts/simulate_razorpay_dispute.py duplicate
py scripts/simulate_razorpay_dispute.py invalid-signature
py scripts/simulate_razorpay_dispute.py unknown-merchant
py scripts/simulate_razorpay_dispute.py expired
py scripts/simulate_razorpay_dispute.py out-of-order
```

Lifecycle scenarios are `action-required`, `under-review`, `won`, `lost`, and `closed`. The simulator signs the exact JSON sent to the real `/webhook/razorpay` endpoint. Both the script and development router reject production mode, and simulator delivery is restricted to a loopback `/webhook/razorpay` URL. It never contacts Razorpay or creates a real dispute; `disp_SIM_...` IDs exist only in ChargeGuard.

Run Razorpay-focused tests with `py -m pytest -q tests/test_razorpay_webhooks.py tests/test_razorpay_event_recovery.py tests/test_razorpay_integration.py tests/test_razorpay_reconciliation.py tests/test_razorpay_simulator.py`.

The current synchronized store is adequate for one-process staging when `CHARGEGUARD_STORE_PATH` is configured. Production multi-worker deployment must replace it with a shared transactional database plus a queue/outbox so event claims and workflow scheduling remain atomic across processes.

## Staging Deployment

ChargeGuard supports a recoverable, single-instance Razorpay Test Mode staging deployment. The webhook verifies and validates the exact signed body, persists a PII-minimized event, queues its ID, and returns `202` before Razorpay enrichment or LangGraph execution begins.

1. Configure these required staging values in the deployment secret manager or `.env`:

   ```env
   PORT=8000
   ENVIRONMENT=production
   API_KEY=<strong-internal-api-key>
   RAZORPAY_KEY_ID=<rzp_test_key_id>
   RAZORPAY_KEY_SECRET=<test-mode-api-secret>
   RAZORPAY_WEBHOOK_SECRET=<separate-webhook-secret>
   RAZORPAY_WEBHOOK_ENABLED=true
   RAZORPAY_RECOVER_PENDING_ON_STARTUP=true
   RAZORPAY_STARTUP_RECOVERY_LIMIT=25
   RAZORPAY_SIMULATOR_ENABLED=false
   CHARGEGUARD_STORE_PATH=/var/data/chargeguard_store.json
   CHARGEGUARD_USE_STUBS=true
   RAZORPAY_USE_STUBS=false
   MODEL_PATH=./ml/artifacts/win_probability_model.pkl
   ```

   `RAZORPAY_KEY_SECRET` authenticates outbound REST calls. `RAZORPAY_WEBHOOK_SECRET` validates inbound webhook signatures; keep them separate and never commit either one.

2. Build the image. The deterministic baseline model is trained during the build, and a training failure fails the build:

   ```bash
   docker build -t chargeguard-staging .
   ```

3. Run exactly one application process with a persistent disk mounted at `/var/data`:

   ```bash
   docker volume create chargeguard-data
   docker run --rm --name chargeguard-staging \
     --env-file .env \
     -p 8000:8000 \
     -v chargeguard-data:/var/data \
     chargeguard-staging
   ```

   The JSON store is not safe for multiple application processes. Multi-worker production requires a shared transactional database, durable queue/outbox, and atomic event claim plus job creation.

4. Confirm the selected port is healthy:

   ```bash
   curl http://127.0.0.1:8000/health
   ```

   The response must include `"status":"ok"` and `"model_loaded":true`.

5. Map the Razorpay account before enabling delivery. Use the exact `account_id` emitted by Razorpay:

   ```bash
   curl -X POST https://<deployed-host>/merchants \
     -H "X-API-Key: $API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"merchant_id":"merchant_001","name":"Example Merchant","vertical":"ecommerce","payment_provider":"razorpay","razorpay_account_id":"acc_...","freshdesk_domain":"","average_order_value":2500,"chargeback_history_count":0}'
   ```

6. Configure the Razorpay Test Mode Dashboard callback:

   ```text
   https://<deployed-host>/webhook/razorpay
   ```

   Enable `payment.dispute.created`, `payment.dispute.action_required`, `payment.dispute.under_review`, `payment.dispute.won`, `payment.dispute.lost`, and `payment.dispute.closed`. Enter the same separate webhook secret configured as `RAZORPAY_WEBHOOK_SECRET`.

7. Inspect safe event metadata, including unresolved account mappings:

   ```bash
   curl "https://<deployed-host>/internal/razorpay/events?processing_state=unresolved" \
     -H "X-API-Key: $API_KEY"
   ```

   Stored processing payloads are not returned by this endpoint.

8. After correcting a merchant mapping or temporary failure, retry one eligible event or process a bounded batch:

   ```bash
   curl -X POST "https://<deployed-host>/internal/razorpay/events/<event-id>/retry" \
     -H "X-API-Key: $API_KEY"

   curl -X POST "https://<deployed-host>/internal/razorpay/process-pending?limit=25" \
     -H "X-API-Key: $API_KEY"
   ```

9. Reconcile missed or drifted disputes through the same normalization/upsert path:

   ```bash
   curl -X POST "https://<deployed-host>/internal/razorpay/reconcile" \
     -H "X-API-Key: $API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"merchant_id":"merchant_001","count":100}'
   ```

### Final Staging Checklist

Required variables are `ENVIRONMENT=production`, a strong `API_KEY`, Razorpay Test Mode `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET`, a separate `RAZORPAY_WEBHOOK_SECRET`, `RAZORPAY_WEBHOOK_ENABLED=true`, `RAZORPAY_SIMULATOR_ENABLED=false`, `CHARGEGUARD_USE_STUBS=true`, `MODEL_PATH=./ml/artifacts/win_probability_model.pkl`, and `CHARGEGUARD_STORE_PATH=/var/data/chargeguard_store.json`.

Run one application instance with one worker, mount a persistent volume at `/var/data`, expose `/health`, and configure the public Razorpay callback as `/webhook/razorpay`. Subscribe to `payment.dispute.created`, `payment.dispute.action_required`, `payment.dispute.under_review`, `payment.dispute.won`, `payment.dispute.lost`, and `payment.dispute.closed`.

### Recovery After Restart

By default, one daemon worker schedules up to 25 recoverable Razorpay events after startup. Configure this with `RAZORPAY_RECOVER_PENDING_ON_STARTUP=true` and `RAZORPAY_STARTUP_RECOVERY_LIMIT=25`; the limit is constrained to 1-100. It considers received, queued, failed, stale processing, and unresolved events whose account now maps to a merchant. It never retries successful or ignored events, and provider or graph work does not delay application startup.

Use the protected manual endpoint as an operator fallback:

```bash
curl -X POST "https://<deployed-host>/internal/razorpay/process-pending?limit=25" \
  -H "X-API-Key: $API_KEY"
```

Inspect failures or unresolved account mappings, then retry one corrected event:

```bash
curl "https://<deployed-host>/internal/razorpay/events?processing_state=failed" \
  -H "X-API-Key: $API_KEY"
curl "https://<deployed-host>/internal/razorpay/events?processing_state=unresolved" \
  -H "X-API-Key: $API_KEY"
curl -X POST "https://<deployed-host>/internal/razorpay/events/<event-id>/retry" \
  -H "X-API-Key: $API_KEY"
```

Deploying ChargeGuard does not create a Razorpay chargeback. Razorpay creates dispute events from its payment/card-network lifecycle and delivers them to the configured callback. The local simulator creates only signed Razorpay-shaped test events with `disp_SIM_...` IDs; it never contacts Razorpay, accepts a dispute, contests a dispute, or creates a real provider record.

Razorpay Test API keys do not expose a public create-dispute endpoint. Deployment only makes ChargeGuard's webhook reachable; the simulator does not create a real Razorpay dispute. JSON persistence remains appropriate only for one-process staging. Multi-worker production requires a shared transactional database and durable queue/outbox.

## Generated Outputs

Rebuttal PDFs are written to:

```text
./output/rebuttals
```

Learning artifacts are written under:

```text
./ml/artifacts
```

These generated outputs should not be committed unless intentionally adding a fixture.

## Roadmap

Near-term:

- Replace local JSON API persistence with database-backed production storage.
- Complete live food and quick-commerce platform integrations.
- Add Claude Vision verification for delivery photos.
- Add authenticated merchant configuration management.
- Add production filing adapters for card-network portals.

Model roadmap:

- Continue with logistic regression until enough real outcomes are collected.
- Move to XGBoost after 100+ real labelled cases.
- Track win rate, decision accuracy, evidence coverage, and quality-loop pass rate.

## Known Limitations

- Dispute storage is in-memory by default and does not survive restarts unless optional local JSON persistence is configured.
- The filing layer is a local stub and is not connected to real card-network APIs.
- The win-probability model is seeded with synthetic data that decays out as real WIN/LOSS outcomes accumulate.
- API authentication currently uses a shared environment-configured API key rather than per-merchant identity and authorization.

## License

Private project. Add a license before public distribution.
