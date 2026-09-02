# ChargeGuard Agent Guide

This file is the operating manual for AI coding agents working in this repository. Read it before changing code. The executable code, schemas, and tests are authoritative. `context.md` and `branches.md` are historical notes and contain stale branch, dependency, and architecture information.

## Project Mission

ChargeGuard automates chargeback investigation and dispute preparation for Indian merchants. It receives a normalized chargeback or a signed Razorpay dispute webhook, gathers evidence, predicts the probability of winning, computes the expected value of fighting, and chooses one of three actions:

- `FIGHT`: build and quality-check a rebuttal, then record a local filing-stub confirmation.
- `ACCEPT`: do not contest because the expected value is not positive enough.
- `ESCALATE_DEGRADED`: require human review because evidence, model data, payment rail, card network, reason code, deadline, or provider enrichment is insufficient.

The product is India-first. INR is the default operating currency, Razorpay is the primary payment integration, and Visa, Mastercard, and RuPay playbooks exist. Stripe remains supported as a secondary payment adapter. AMEX can be represented in API data but has no automated rebuttal playbook and should be escalated when no supported playbook exists.

## Current Technical Baseline

- Python target: 3.11
- API: FastAPI
- Workflow: LangGraph
- ML: scikit-learn logistic regression
- PDF generation: ReportLab
- HTTP clients: httpx
- Persistence: process-local synchronized store, optionally persisted to one JSON file
- Deployment: Dockerfile plus a single-service `docker-compose.yml`
- CI: `.github/workflows/ci.yml`, Python 3.11, Poetry, full pytest suite
- Last verified baseline at commit `2b0c1c4`: `198 passed`

The pass count is a historical checkpoint, not a permanent expectation. Always run the current suite and report the current result.

## Source Of Truth

Use these files in this order when behavior is unclear:

1. `core/state.py`: workflow and merchant data contracts.
2. `core/graph.py`: actual graph nodes, branches, and terminal paths.
3. `api/schemas.py`: accepted and returned API shapes.
4. The route or agent implementing the behavior.
5. Tests covering that behavior.
6. `README.md` for user-facing instructions.

Do not trust old statements in `context.md` or `branches.md` without checking the code. Examples of stale historical claims include old branches, removed modules, and dependencies that are no longer present.

## Repository Map

```text
main.py                         FastAPI app, router registration, /health
api/                            Authentication, routes, store, Razorpay ingestion
agents/                         LangGraph nodes and evidence agents
agents/evidence/                Payment, shipping, device, support, and consortium evidence
core/state.py                   ChargebackState and MerchantProfile contracts
core/graph.py                   Compiled LangGraph workflow
core/outcomes.py                Filed-only WIN/LOSS invariant
core/currency.py                Static environment-configured FX conversion
core/deadlines.py               Network filing deadline handling
integrations/                   External provider clients and optional LLM adapters
ml/features.py                  Deterministic feature extraction
ml/model.py                     Versioned WinProbabilityModel artifact
ml/subscores.py                 Model-coefficient-backed explanatory sub-scores
ml/feedback.py                  Filed outcome storage and retraining
ml/synthetic_data.py            Deterministic seed data
documents/playbooks/            Visa, Mastercard, and RuPay reason-code playbooks
documents/pdf_builder.py        Deterministic ReportLab output
analytics/                      Merchant dispute monitoring calculations
static/                         Local dashboard assets
scripts/                        Demo, simulator, and model checks
tests/                          Unit and integration-style tests
```

## Central State Contract

Every graph node receives and returns `core.state.ChargebackState`. Keep this as the single workflow contract. Do not introduce an independent parallel case object without a specific migration plan.

Important state groups:

- Input: IDs, provider metadata, reason code, card network, amount, currency, deadline, merchant.
- Orchestration: investigation plan and food-agent routing.
- Evidence: transaction, shipping, communications, device, consortium, delivery photo, timeline.
- Degradation: `evidence_collection_degraded` and `degraded_reasons`.
- Intelligence: win probability, expected value, sub-scores, contradiction flags, decision, reasoning.
- Response: PDF path, quality result, filing confirmation, filing timestamp.
- Learning: final outcome, outcome reason, and outcome timestamp.

Provider-native and network-native concepts must stay distinct:

- `provider_reason_code` is the Razorpay reason.
- `network_reason_code` is a Visa, Mastercard, or RuPay playbook key when reliably supplied.
- Never translate a provider reason into a network reason by guessing.
- UPI is `payment_rail=UPI`; it is not RuPay and does not imply a card network.

## LangGraph Workflow

The compiled graph is `core.graph.app`.

### Standard path

```text
orchestrator
  -> transaction_evidence
  -> shipping_evidence
  -> device_evidence
  -> comms_evidence
  -> consortium_evidence
  -> optional delivery_photo_evidence
  -> optional order_timeline_evidence
  -> scoring
```

Food-delivery and quick-commerce cases can use the two additional food evidence nodes.

### Overdue path

When `investigation_plan.priority == "overdue"`, the graph uses only transaction and shipping evidence before scoring:

```text
orchestrator
  -> expedited_transaction_evidence
  -> expedited_shipping_evidence
  -> scoring
```

The scoring reasoning must identify this as an expedited partial-evidence decision.

### Decision paths

```text
FIGHT
  -> rebuttal_builder
  -> quality_check
  -> filing when approved
  -> rebuttal_builder retry when auto-fixable
  -> human_escalation after 3 attempts or a non-fixable failure

ACCEPT
  -> accept_and_log
  -> END

ESCALATE_DEGRADED
  -> human_escalation
  -> END
```

Learning runs only when the outcome is a real `WIN` or `LOSS` and `is_filed_dispute(state)` is true.

## Decision And Outcome Semantics

Do not confuse a decision with an adjudicated result:

- `FIGHT` means ChargeGuard decided to contest. It is not a win.
- `ACCEPT` means ChargeGuard chose not to contest. It sets `ACCEPTED_NO_CONTEST`, not `LOSS`.
- `ESCALATE_DEGRADED` means automation could not safely decide. It remains pending for human handling.
- `WIN` means a filed representment was adjudicated in the merchant's favor.
- `LOSS` means a filed representment was adjudicated against the merchant.
- `PENDING` means no final network outcome exists.

`core.outcomes.record_adjudicated_outcome` is the shared gate for manual and provider outcomes. It rejects WIN/LOSS updates unless all filed-case conditions hold:

- decision is `FIGHT`;
- quality was approved;
- `filed_at` exists;
- filing confirmation begins with `filed_`.

Never train on `ACCEPTED_NO_CONTEST`, pending, escalated, unfiled, or synthetic provider lifecycle states.

## Scoring And ML

The deterministic decision rule in `agents/scoring.py` is:

```text
expected_value = win_probability * dispute_amount - response_cost_in_case_currency
decision = FIGHT when expected_value > FIGHT_EV_THRESHOLD, otherwise ACCEPT
```

If evidence collection is degraded or the model cannot be loaded/executed, the decision must be `ESCALATE_DEGRADED`. Do not silently use a zero probability to force `ACCEPT`.

Currency rates are expressed as currency units per USD. Defaults are USD 1.0 and INR 83.0. Configure overrides with `RESPONSE_COST_FX_RATES`, for example:

```env
RESPONSE_COST_FX_RATES=INR:83.0,USD:1.0
```

Unknown FX rates warn and fail open by treating the amount as already denominated in the target currency. Model amount buckets currently support INR and USD explicitly. Add a tested bucket scale before accepting another currency in automated scoring.

The model features come only from collected state. The two explanatory scores use the loaded model's learned coefficient directions:

- `third_party_fraud_indicators`
- `identity_continuity`

They explain existing model signals and must not become independent untrained decision rules.

### Training lifecycle

1. Initial training uses deterministic synthetic rows.
2. Only real filed WIN/LOSS records enter `outcomes.json`.
3. Retraining occurs after `RETRAIN_RECORD_THRESHOLD` new records.
4. Synthetic rows decay by:

   ```text
   max(0, SYNTHETIC_SEED_COUNT - real_record_count * SYNTHETIC_DECAY_PER_REAL_RECORD)
   ```

5. Defaults are 200 seed rows and decay of 4 per real record, reaching zero at 50 records.
6. `training_metadata.json` records the real/synthetic split.

Generated model and learning artifacts belong under `ml/artifacts/` and are ignored by Git.

## Evidence Providers

Current provider ownership:

| Evidence | Provider/client |
| --- | --- |
| Payments | Razorpay or Stripe |
| Shipping | Shiprocket, with Delhivery support |
| Communications | Freshdesk and Gmail |
| Device/fraud | SEON |
| Consortium alerts | Ethoca and Verifi |
| Food/quick-commerce | Stub-first platform adapter |
| Delivery image analysis | Optional Claude Vision adapter |

`CHARGEGUARD_USE_STUBS` is the global provider default. A provider-specific variable such as `RAZORPAY_USE_STUBS=false` overrides the global setting for that provider. Use one live override at a time during provider validation.

On provider failure, an evidence agent must not fabricate maximally suspicious evidence. It should use neutral missing evidence, mark degradation, append a clear reason, and allow scoring to escalate.

Merchant-specific support credentials are selected by a non-secret `support_connector_ref`. Secrets stay in environment variables such as `CHARGEGUARD_CONNECTOR_ACME_FRESHDESK_API_KEY`; they must not be stored in merchant API payloads.

## Optional LLM Boundaries

LLM features are additive and must remain outside deterministic decisioning:

- delivery photo verification;
- human review case summary;
- optional rebuttal narrative;
- read-only portfolio assistant.

LLM output must not alter `win_probability`, `expected_value`, or `decision`. Keep calls deterministic, grounded in redacted case data, and fail safely when unavailable. Do not claim that Gmail/Freshdesk retrieval requires an LLM; those integrations retrieve structured records directly and optional LLM features summarize or narrate afterward.

## API Surface And Authentication

All routes except `/health` and the Razorpay webhook require `X-API-Key`, unless explicitly noted below. `API_KEY` supports a comma-separated list. Missing or invalid keys return 401.

| Endpoint | Authentication | Purpose |
| --- | --- | --- |
| `GET /health` | None | Model and stub-mode status |
| `POST /merchants` | `X-API-Key` | Register merchant and provider mapping |
| `GET /merchants/{id}` | `X-API-Key` | Retrieve merchant and monitoring ratios |
| `POST /webhook/chargeback` | `X-API-Key` | Submit normalized internal chargeback |
| `POST /webhook/razorpay` | Razorpay HMAC signature | Receive provider-native dispute event |
| `GET /disputes` | `X-API-Key` | List redacted disputes |
| `GET /disputes/{id}` | `X-API-Key` | Retrieve redacted dispute detail |
| `GET /disputes/{id}?include_raw=true` | API key plus `X-Internal-Token` | Internal raw evidence access |
| `GET /disputes/{id}/summary` | `X-API-Key` | Optional human-review summary |
| `POST /disputes/{id}/outcome` | `X-API-Key` | Record filed WIN/LOSS |
| `GET /stats` | `X-API-Key` | Portfolio aggregates |
| `POST /assistant/query` | `X-API-Key` | Read-only grounded assistant |
| `GET /internal/razorpay/events` | `X-API-Key` | Inspect provider event processing |
| `POST /internal/razorpay/events/{event_id}/retry` | `X-API-Key` | Retry one eligible provider event |
| `POST /internal/razorpay/process-pending` | `X-API-Key` | Enqueue a bounded recovery batch |
| `POST /internal/razorpay/reconcile` | `X-API-Key` | Reconcile Razorpay disputes through REST |
| `/dev/razorpay-simulator/*` | `X-API-Key` | Development-only signed event simulator |
| `/dashboard` | Static route | Local dashboard assets; API calls remain protected |

The normalized webhook and assistant use process-local token buckets. The Razorpay webhook does not use `X-API-Key`; provider authentication is its exact-body signature.

Dispute responses remove nested evidence `raw` objects and transaction email, IP, and device ID by default. Do not weaken that redaction. Raw access requires both `include_raw=true` and a matching `INTERNAL_API_TOKEN` header.

## Razorpay Architecture

### Inbound webhook security

`POST /webhook/razorpay` performs these steps in order:

1. Confirm `RAZORPAY_WEBHOOK_ENABLED`.
2. Enforce `RAZORPAY_WEBHOOK_MAX_BODY_BYTES`.
3. Read the exact raw request bytes.
4. Verify `X-Razorpay-Signature` using HMAC-SHA256 and `RAZORPAY_WEBHOOK_SECRET`.
5. Parse and structurally validate the event only after signature verification.
6. Use `x-razorpay-event-id` as the idempotency key; use a deterministic SHA-256 fallback if absent.
7. Persist only the allowlisted, PII-minimized fields needed for deferred processing.
8. Atomically claim and move the event to `queued` before scheduling work.
9. Add the event ID to FastAPI `BackgroundTasks`.
10. Return `202 Accepted` without merchant resolution, REST calls, LangGraph execution, or PDF work.

The background processor atomically moves `queued` to `processing`, resolves the merchant, enriches missing payment/card data, normalizes and upserts the dispute, schedules the graph only when eligible, and records a terminal provider-event state. `received`, `queued`, and `processing` never have `processed_at`; terminal states do. Failed, unresolved, queued, and stale-processing events are recoverable through the protected internal endpoints.

Do not parse and reserialize the body before signature verification. Do not add `X-API-Key` authentication to this endpoint.

### Enabled events

Configure all six Razorpay events:

- `payment.dispute.created`
- `payment.dispute.action_required`
- `payment.dispute.under_review`
- `payment.dispute.won`
- `payment.dispute.lost`
- `payment.dispute.closed`

Webhooks are primary. Reconciliation is the safety net for missed delivery and state drift.

### Idempotency and ordering

Provider event records include event ID, event type, dispute ID, payload hash, processing state, timestamps, attempt count, and failure reason. Duplicate completed/in-progress events are ignored. Failed events and stale claims can be reclaimed after `PROVIDER_EVENT_CLAIM_TIMEOUT_SECONDS`.

Razorpay event ordering is not guaranteed. The service prevents terminal-state regression, ignores stale non-terminal metadata, rejects conflicting WIN/LOSS outcomes, and does not invent a WIN or LOSS from `payment.dispute.closed`.

### Automation eligibility

A created event is scheduled into the graph only when all required automation data is reliable. Human review is required for conditions including:

- payment enrichment failure;
- non-card rail such as UPI;
- missing card network;
- missing network reason code;
- missing supported playbook;
- missing order ID;
- missing or overdue response deadline;
- lifecycle event arriving before creation.

Unknown merchant account IDs are recorded as `unresolved` and acknowledged without scheduling. Inspect them through the protected internal events endpoint.

### Outbound Razorpay REST support

`integrations/razorpay.py` uses Razorpay Basic Authentication, bounded timeouts, and normalized errors. It supports:

- payment fetch, including `expand[]=card`;
- order fetch;
- dispute list and fetch;
- dispute evidence document upload;
- dispute accept;
- dispute contest/draft submission.

Accept and contest are consequential provider actions. They are client capabilities but are not automatically invoked by the local filing stub or simulator. Do not claim real network filing until a reviewed production adapter calls and verifies these operations.

## Environment Configuration

Never commit `.env`, API keys, webhook secrets, access tokens, customer PII, or generated provider payloads. `.env` is Git-ignored. `.env.example` must contain placeholders only.

Minimum deterministic local configuration:

```env
ENVIRONMENT=development
API_KEY=replace-with-a-local-key
CHARGEGUARD_USE_STUBS=true
RAZORPAY_WEBHOOK_SECRET=replace-with-a-local-webhook-secret
RAZORPAY_WEBHOOK_ENABLED=true
RAZORPAY_SIMULATOR_ENABLED=true
MODEL_PATH=./ml/artifacts/win_probability_model.pkl
CHARGEGUARD_STORE_PATH=./data/chargeguard_store.json
```

Razorpay Test Mode additionally requires:

```env
RAZORPAY_USE_STUBS=false
RAZORPAY_KEY_ID=rzp_test_...
RAZORPAY_KEY_SECRET=...
```

The webhook secret is separate from the API key secret:

- `RAZORPAY_KEY_SECRET` authenticates ChargeGuard's outbound REST calls.
- `RAZORPAY_WEBHOOK_SECRET` verifies inbound webhook signatures.

Docker Compose loads `.env` through `env_file`. A direct native Python process does not automatically load `.env` because `python-dotenv` is not a dependency. Export variables into the process environment before starting natively, or use Docker Compose.

## First-Time Local Setup

Perform these steps in order:

1. Confirm repository and branch:

   ```powershell
   Set-Location D:\ChargeGuard\chargeguard
   git status --short --branch
   git remote -v
   ```

2. Install the supported Python 3.11 and Poetry.

3. Install dependencies:

   ```powershell
   poetry install
   ```

4. Create `.env` from `.env.example` and replace only local placeholders. Never stage it.

5. Export `.env` values for a native process, or use Docker Compose. In PowerShell, simple `KEY=value` lines can be loaded with:

   ```powershell
   Get-Content .env | ForEach-Object {
     if ($_ -match '^([^#][^=]*)=(.*)$') {
       [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2], 'Process')
     }
   }
   ```

6. Train the baseline model:

   ```powershell
   poetry run python -m ml.train
   ```

7. Start from the repository root, where `main.py` exists:

   ```powershell
   poetry run uvicorn main:app --reload --port 8000
   ```

8. Check health:

   ```powershell
   curl.exe http://127.0.0.1:8000/health
   ```

   Expected shape:

   ```json
   {"status":"ok","model_loaded":true,"stub_mode":true}
   ```

9. Open API docs at `http://127.0.0.1:8000/docs` and the dashboard at `http://127.0.0.1:8000/dashboard`.

## Docker Setup

1. Ensure `.env` exists.
2. Build and start. The image build runs `python -m ml.train` after copying the application, so a missing or invalid baseline model fails the build:

   ```powershell
   docker compose up --build -d
   ```

3. Verify health on `PORT` (default 8000):

   ```powershell
   curl.exe http://127.0.0.1:8000/health
   ```

4. Inspect startup errors:

   ```powershell
   docker compose logs api
   ```

5. Stop without deleting source data:

   ```powershell
   docker compose down
   ```

Compose mounts `chargeguard-data` at `/var/data` and defaults `CHARGEGUARD_STORE_PATH` to `/var/data/chargeguard_store.json`. Run only one application process with this JSON store. There is no Neo4j service in Compose; do not reintroduce one unless a concrete persisted graph feature is implemented and tested.

## Internal Artificial Chargeback Test

Use this path to test ChargeGuard without contacting Razorpay:

1. Start with stubs and a trained model.
2. Register a merchant using `POST /merchants` and `X-API-Key`.
3. Submit a future-dated case to `POST /webhook/chargeback`.
4. Poll `GET /disputes/{chargeback_id}` until status is `completed` and decision is populated.
5. Inspect decision, probability, expected value, sub-scores, contradiction flags, and PDF path.
6. For a FIGHT case, record a real-looking test outcome only after the filing stub completed.
7. Verify ACCEPT produces `ACCEPTED_NO_CONTEST` and does not grow the learning dataset.
8. Verify degraded evidence produces `ESCALATE_DEGRADED`, pending outcome, and no learning record.

This tests application behavior. It does not mimic Razorpay's authenticated delivery contract.

## Razorpay Simulator Test

The simulator exercises the real HTTP webhook handler with Razorpay-shaped signed JSON but never contacts Razorpay.

1. Set development mode, simulator enabled, API key, and webhook secret.
2. Register a Razorpay merchant with a unique `razorpay_account_id`.
3. Start the API on port 8000.
4. Run scenarios:

   ```powershell
   py scripts/simulate_razorpay_dispute.py card-created
   py scripts/simulate_razorpay_dispute.py upi-created
   py scripts/simulate_razorpay_dispute.py duplicate
   py scripts/simulate_razorpay_dispute.py invalid-signature
   py scripts/simulate_razorpay_dispute.py unknown-merchant
   py scripts/simulate_razorpay_dispute.py expired
   py scripts/simulate_razorpay_dispute.py out-of-order
   ```

5. Exercise lifecycle scenarios: `action-required`, `under-review`, `won`, `lost`, and `closed`.
6. Inspect canonical events at `GET /internal/razorpay/events`.
7. Confirm `disp_SIM_...` IDs never leave ChargeGuard.

Simulator safeguards must remain intact:

- disabled when `ENVIRONMENT=production`;
- delivery target restricted to loopback `/webhook/razorpay`;
- simulator metadata accepted only for simulator IDs in development/test;
- no accept or contest provider call.

## Real Razorpay Test Mode Onboarding

Do not call a connection live until every step below is completed:

1. Obtain Razorpay Test Mode `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET` from the merchant dashboard.
2. Generate a separate webhook secret and set `RAZORPAY_WEBHOOK_SECRET`.
3. Deploy ChargeGuard on a public HTTPS host. Razorpay cannot deliver to localhost.
4. Set `ENVIRONMENT` to the staging value used by the deployment.
5. Set `RAZORPAY_SIMULATOR_ENABLED=false` for the real provider test.
6. Set `RAZORPAY_WEBHOOK_ENABLED=true` and `RAZORPAY_USE_STUBS=false`.
7. Register the merchant with `payment_provider=razorpay` and the exact account ID emitted by Razorpay webhook events.
8. Configure callback `https://<public-host>/webhook/razorpay` in the Razorpay Test Mode dashboard.
9. Select all six dispute lifecycle events listed above.
10. Enter the same webhook secret in Razorpay and ChargeGuard.
11. Trigger or deliver a Test Mode dispute event.
12. Confirm Razorpay receives a 2xx response.
13. Inspect `/internal/razorpay/events` for `scheduled`, `completed`, `ignored`, `unresolved`, or failure state.
14. Poll `/disputes/{id}` and verify provider IDs, rail, network, deadline, decision, and degradation reasons.
15. Run `/internal/razorpay/reconcile` and confirm duplicate/upsert behavior.
16. Test duplicate delivery, delayed delivery, and out-of-order lifecycle events.
17. Confirm no real-money/live key was used.

Real connectivity is proven only by Razorpay Dashboard delivery logs plus ChargeGuard's matching canonical event record. Unit tests and the simulator are not proof of a live provider connection.

## Testing Procedure For Every Code Change

Follow this sequence without skipping steps:

1. Read the target implementation and its existing tests.
2. Record `git status --short --branch`; preserve unrelated user changes.
3. Reproduce the bug or define the behavior with a focused test.
4. Make only the scoped implementation change.
5. Run the smallest relevant tests first.
6. Fix failures before moving to broader tests.
7. Run the full suite:

   ```powershell
   poetry run pytest -q
   ```

   If Poetry is unavailable but dependencies are installed:

   ```powershell
   py -m pytest -q
   ```

8. Run `git diff --check`.
9. Inspect `git diff --stat` and the actual diff for accidental generated files or secrets.
10. Report the exact pass/fail count and warnings that matter.

Focused commands:

```powershell
py -m pytest -q tests/test_graph.py
py -m pytest -q tests/test_api.py
py -m pytest -q tests/test_scoring.py tests/test_features.py tests/test_feedback.py
py -m pytest -q tests/test_razorpay_webhooks.py tests/test_razorpay_integration.py tests/test_razorpay_reconciliation.py tests/test_razorpay_simulator.py
```

Provider integration tests use mocks/stubs and must never make unapproved network requests or require real secrets.

## Change Discipline

For every requested implementation:

1. Confirm the current branch. Do not assume a branch mentioned in historical notes is checked out.
2. Do not commit directly to `main` or `develop`; create or use the requested feature/fix branch.
3. Check for uncommitted user work and preserve it.
4. Search imports and call sites before deleting or changing contracts.
5. Prefer existing patterns and helpers.
6. Keep `ChargebackState`, API schemas, serialization, and tests synchronized.
7. Add tests proportional to behavioral risk.
8. Do not refactor unrelated modules.
9. Do not alter provider semantics to make a test pass.
10. Do not expose PII or secrets in logs, API responses, fixtures, commits, or final messages.
11. Do not commit generated PDFs, model artifacts, outcome data, cache files, or `.env`.
12. Commit only when explicitly requested.
13. Push only when explicitly requested.
14. Never merge into `main` or `develop` without explicit instruction.

## Commit And Push Procedure

When the user explicitly requests a commit and push:

1. Run the full tests.
2. Run `git diff --check`.
3. Review `git status` and `git diff --stat`.
4. Confirm `.env` is ignored:

   ```powershell
   git check-ignore -v .env
   ```

5. Stage only intended files. Use `git add --all` only after reviewing every change.
6. Verify staged content with `git diff --cached --stat` and `git diff --cached --check`.
7. Create one clear imperative/conventional commit message.
8. Push the current feature branch explicitly.
9. Verify local and remote hashes:

   ```powershell
   git rev-parse HEAD
   git rev-parse origin/$(git branch --show-current)
   git status --short --branch
   ```

10. Report branch, commit hash, test summary, push result, and whether the worktree is clean.

## Production Readiness Constraints

The current project is suitable for local demos, automated tests, and single-process staging. It is not ready for unattended multi-merchant production operation until these gaps are addressed:

- Replace in-memory/JSON storage with a shared transactional database.
- Add a durable queue or outbox for webhook-to-workflow scheduling.
- Replace shared API-key authentication with merchant identity, authorization, rotation, and audit controls.
- Store provider secrets in a managed secret store.
- Replace the local filing stub with reviewed provider/network submission adapters.
- Add production observability, alerting, retries, dead-letter handling, and operator remediation.
- Validate model calibration with representative real adjudicated outcomes.
- Establish approved FX rate ownership and update process.
- Complete privacy, retention, consent, and access-control review for PII and evidence.
- Perform Razorpay Test Mode end-to-end certification before using live keys.

Do not describe these items as already implemented.

## Common Troubleshooting

### `Could not import module "main"`

- Run uvicorn from the repository root containing `main.py`.
- Confirm the virtual environment has dependencies installed.
- Run `py -c "import main; print(main.app.title)"` to expose the real import error.

### `/health` says `degraded`

- Check `MODEL_PATH`.
- Run `python -m ml.train` in the same host/container environment.
- Confirm the artifact is readable and matches the current feature schema.

### API returns 401

- Confirm the server process received `API_KEY`.
- Send the exact value in `X-API-Key`.
- Remember that direct native startup does not automatically load `.env`.

### Dashboard shows no disputes

- Confirm a merchant was registered.
- Confirm the webhook returned 202 or a meaningful 200 provider status.
- Poll the matching chargeback ID.
- Check `CHARGEGUARD_STORE_PATH` and whether the current process/container uses the expected file.
- Inspect provider events for unknown-account `unresolved` records.

### Razorpay signature fails

- Confirm dashboard and server use the same webhook secret.
- Verify HMAC over exact raw bytes, not parsed JSON.
- Do not use `RAZORPAY_KEY_SECRET` as the webhook secret unless deliberately configured identically.

### Razorpay webhook is unresolved

- Read the event's `account_id` from the protected event record.
- Register or correct the merchant's `razorpay_account_id`.
- Retry/reconcile after mapping rather than editing provider payloads.

### Case escalates instead of fighting

- Inspect `degraded_reasons` and `decision_reasoning`.
- Confirm model artifact, card rail, card network, network reason code, playbook, order ID, and response deadline.
- UPI and unsupported/missing card playbooks are intentionally human-reviewed.

### PDF path looks malformed in a browser alert

- Read `rebuttal_document_path` from the JSON response.
- Treat it as a filesystem path relative to `REBUTTAL_OUTPUT_DIR`.
- Do not reconstruct it from line-wrapped alert text.

## Definition Of Done

A task is complete only when:

- requested behavior is implemented;
- focused tests prove the behavior changed;
- full tests pass or a concrete external blocker is reported;
- no unrelated files were refactored;
- no secrets, PII, or generated artifacts were added;
- documentation and examples match the code when behavior changed;
- branch, commit, and push state are accurately reported;
- no merge occurred unless explicitly requested.
