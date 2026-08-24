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
- Merchant registration and retrieval endpoints
- Chargeback webhook endpoint
- In-memory dispute store for local development, with optional JSON persistence
- LangGraph workflow with all core nodes wired
- Orchestrator playbook routing
- Transaction evidence with Razorpay and Stripe support (adapter pattern designed to extend to additional gateways)
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
- FastAPI
- LangGraph
- Anthropic Claude API for deterministic language and vision tasks
- LangSmith tracing hooks
- Neo4j integration layer
- ReportLab for PDF generation
- scikit-learn baseline ML model
- XGBoost dependency included for future model upgrade
- httpx for provider integrations
- pytest and pytest-asyncio
- Docker Compose for local Neo4j

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

## Getting Started

### Prerequisites

- Python 3.11
- Poetry
- Docker, optional for Neo4j
- Provider sandbox credentials, optional

### Install

```bash
poetry install
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
poetry run uvicorn main:app --reload --port 8001
```

Health check:

```bash
curl http://127.0.0.1:8001/health
```

Expected response:

```json
{"status":"ok"}
```

## API Usage

### Register A Merchant

```bash
curl -X POST http://127.0.0.1:8001/merchants \
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
curl http://127.0.0.1:8001/disputes
```

### Get A Dispute

```bash
curl http://127.0.0.1:8001/disputes/cb_demo_001
```

### Record Final Outcome

```bash
curl -X POST http://127.0.0.1:8001/disputes/cb_demo_001/outcome \
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
