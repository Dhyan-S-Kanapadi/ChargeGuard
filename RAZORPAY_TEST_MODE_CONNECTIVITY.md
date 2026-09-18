# Razorpay Test Mode webhook connectivity

Milestone: a signed dispute notification reaches the existing receiver, is
validated and persisted, receives a prompt acknowledgement, then is processed
or gets an explainable unresolved/human-review result. This is not production
certification, a model evaluation, or real dispute submission.

## Reference and payload compatibility

Official documentation checked on 2026-09-18:

- [Dispute events and payloads](https://razorpay.com/docs/webhooks/disputes)
- [Webhook validation and testing](https://razorpay.com/docs/webhooks/validate-test)
- [Dashboard webhook setup](https://razorpay.com/docs/webhooks/setup-edit-payments)

The payment schema accepts the documented empty `notes: []` as `{}`. Dictionary
notes and absent notes retain existing behavior. Non-empty lists, null, strings,
numbers and booleans remain invalid. The receiver authenticates the exact body
before this normalization; the stored hash still covers the original bytes.
Persisted event notes remain restricted to the existing safe allowlist.

The published examples also contain nullable order IDs and fees, no expanded
card network, historical deadlines, and evidence at the dispute-wrapper level
for action-required. The current schema already accepts these shapes. It does
not promote provider evidence references into verified evidence or guess a
network from a card ID/bank. Tests use a representative fixture with synthetic
identifiers/contact details, plus variants covering all six events.

## Separate staging service

Deploy the existing Dockerfile as a **new service**, with one Uvicorn process and
one instance, separate HTTPS hostname and separate persistent disk. Do not change
the reviewer service or its `render.yaml`. Set the following in the new service's
environment settings; angle-bracket values are placeholders, not working secrets.

```dotenv
ENVIRONMENT=staging
API_KEY=<unique-staging-operator-key>
RAZORPAY_WEBHOOK_ENABLED=true
RAZORPAY_WEBHOOK_SECRET=<different-staging-webhook-secret>
RAZORPAY_RECOVER_PENDING_ON_STARTUP=true
RAZORPAY_STARTUP_RECOVERY_LIMIT=25
RAZORPAY_SIMULATOR_ENABLED=false
PUBLIC_DEMO_ENABLED=false
CHARGEGUARD_DEMO_SEED=false
CHARGEGUARD_USE_STUBS=true
RAZORPAY_USE_STUBS=false
ALLOW_GLOBAL_PAYMENT_CREDENTIAL_FALLBACK=false
ALLOW_GLOBAL_SEON_CREDENTIAL_FALLBACK=false
CHARGEGUARD_CREDENTIAL_ENCRYPTION_KEY=<valid-staging-only-Fernet-key>
CHARGEGUARD_CREDENTIAL_STORE_PATH=/var/data/rzp-connectivity/connector-secrets.json
CHARGEGUARD_STORE_PATH=/var/data/rzp-connectivity/application.json
MODEL_PATH=/var/data/rzp-connectivity/ml/win_probability_model.pkl
TRAINING_DATA_PATH=/var/data/rzp-connectivity/ml/outcomes.json
TRAINING_METADATA_PATH=/var/data/rzp-connectivity/ml/training_metadata.json
PLAYBOOK_STATS_PATH=/var/data/rzp-connectivity/ml/playbook_stats.json
REBUTTAL_OUTPUT_DIR=/var/data/rzp-connectivity/rebuttals
CASE_SUMMARY_USE_STUBS=true
PORTFOLIO_ASSISTANT_USE_STUBS=true
REASON_CLASSIFICATION_ENABLED=false
REBUTTAL_NARRATIVE_ENABLED=false
LLM_DECISION_REVIEW_ENABLED=false
```

Explicitly set every other evidence provider's `*_USE_STUBS=true` if service
settings might contain overrides: STRIPE, SHIPROCKET, DELHIVERY, SEON, ETHOCA,
VERIFI, GMAIL, FRESHDESK, CLAUDE_VISION, FOOD_PLATFORM. Any resulting evidence
from these providers is **synthetic**, and must be presented as such. Inspect
the authenticated `/internal/runtime` response for the effective modes.

`RAZORPAY_USE_STUBS=false` selects HTTP evidence collection; **it does not select
Razorpay Test Mode**. Mode is determined by the merchant's Test API credentials
and the dashboard mode in which the webhook is configured. Connector verification
and missing-payment enrichment can call HTTP regardless of evidence stub flags.
Only enter `rzp_test_<test-key-id>` and its matching Test secret into the connector.
There is no new automatic prohibition of live keys in staging; the operator must
verify their mode. Do not populate global Razorpay credential fallbacks.

Keep the three credential purposes separate:

| Credential | Purpose |
| --- | --- |
| ChargeGuard operator key | Protected app routes in existing operator-auth staging |
| Webhook secret | Exact-body signature verification; same value at Razorpay and ChargeGuard |
| Razorpay Test key ID/secret | Outbound verification and payment enrichment through an owned connector |

Fernet also requires its own valid key, held in the service secret settings,
with persistent writable encrypted storage. Preserve the key across restarts.
The file backend uses process-local synchronization; use one process/instance
even when application metadata uses PostgreSQL. It is not a managed vault.

### Preserve staging authentication

For staging already configured with Supabase, retain
`CHARGEGUARD_AUTH_MODE=supabase`, `CHARGEGUARD_STORE_BACKEND=postgres`, its separate
staging `DATABASE_URL`, `SUPABASE_URL`, and `SUPABASE_PUBLISHABLE_KEY`. Follow
[Supabase setup](SUPABASE_AUTH_SETUP.md) for reviewed migrations and memberships.
Do not switch to operator auth to bypass login/MFA. API_KEY remains required by
runtime configuration but does not authorize protected routes in Supabase mode.
Use an MFA platform admin for merchant creation and Operations/recovery, and a
separate merchant owner with MFA for connectors and case inspection.

For an existing operator-auth staging setup, retain that explicit mode and use
its unique operator key; the local backend can use the isolated JSON path above.
No PostgreSQL provisioning is required solely for this connectivity exercise.
Never point either backend at the demo or production database/storage.

### Isolate every test outcome

Use a fresh database or JSON store, disk, Fernet key, and all four ML paths above.
The Dockerfile builds a synthetic baseline inside the image. Before starting
the experiment, initialize the chosen isolated model path in the staging
container with `python -m ml.train --output /var/data/rzp-connectivity/ml/win_probability_model.pkl`.
This writes only a synthetic staging model. Confirm the directory is writable
and the configured model is available before delivery.

All outcomes from this service are test data, including ordinary `disp_`/`pay_`
IDs. The `disp_SIM_` guard is insufficient for official Test Mode events.
Current non-production learning can write eligible outcomes to its configured
files: path/database isolation is mandatory. Never promote these files, event
records or case snapshots into real training. Do not manually mark sample cases
as filed or WIN/LOSS to make the connectivity test appear successful.

## Operator checklist

1. Review the code/tests, then create the separate staging service from the
   existing Dockerfile. Configure its private storage, model and authentication
   as above. This preparation does not deploy anything automatically.
2. Check `/health`, authenticated `/internal/runtime`, login and Operations.
   Confirm simulator, public demo and seeding are disabled; verify storage
   isolation. Warm the service and avoid sleep/cold starts during delivery.
3. Register a staging merchant with `POST /merchants` (platform admin in Supabase
   mode). Use a staging identity/name and the actual account's exact `account_id`.
   The sample documentation's account ID is not your merchant account.
4. As the merchant owner/operator, call
   `POST /merchants/{merchant_id}/payment-connectors/razorpay` with
   `key_id`, `key_secret`, and `razorpay_account_id`. This operator step makes a
   read-only provider verification request. Require verified connector metadata.
   It tests API credentials, not whether sample dispute/payment IDs exist. Current
   Razorpay verification does not independently prove the supplied account ID;
   match it to the authenticated delivery/account records, never guess it.
5. In the Razorpay dashboard select **Test Mode**, then Accounts & Settings →
   Webhooks → Add New Webhook. Enter
   `https://<separate-staging-host>/webhook/razorpay`, the matching distinct webhook
   secret, and an alert email. The endpoint must be publicly reachable over HTTPS.
6. Subscribe to `payment.dispute.created`, `payment.dispute.action_required`,
   `payment.dispute.under_review`, `payment.dispute.won`, `payment.dispute.lost`,
   and `payment.dispute.closed`. Save/enable the webhook. Selecting subscriptions
   only selects which events to receive; it does not generate disputes.
7. If this account exposes a dashboard sample-notification feature, send one and
   record its delivery ID, timestamp, status and response time. Availability is
   account-dependent. A sample can contain historical/inaccessible IDs and does
   not imply creation of a persistent Test Mode dispute.
8. Separately, if the account supports generating a dispute event against a
   retrievable Test Mode payment, arrange that supported provider test flow.
   Verify its IDs using the same account's Test keys. If unsupported, request
   Razorpay guidance and record this part as unverified; do not invent IDs or
   assume an ordinary test payment automatically creates a dispute.
9. Inspect Operations or `GET /internal/razorpay/events`, matching the returned
   event ID. Poll `GET /disputes/{id}` with the authorized merchant identity.
   Check processing status, safe failure reason, network/reason availability,
   deadline and human-review reasons. Event listings deliberately omit event_data.
10. For `unresolved`, establish the exact merchant account mapping and verified
    connector, then use `POST /internal/razorpay/events/{event_id}/retry`.
    The retry response is HTTP 200 queued, not completion. For recoverable failed
    or stale work use `POST /internal/razorpay/process-pending?limit=25` and inspect
    the records again. Retry rejects ineligible terminal records with 409.
11. If authorized for this staged exercise, use the existing reconciliation
    endpoint for a bounded Test Mode window. It makes provider requests and does
    not prove that a dashboard sample corresponds to a retrievable dispute.
12. Record evidence of acknowledgement and processing separately. Review the
    staging result and disable the test webhook when the exercise ends. Do not
    enable external acceptance, contesting or document upload from the workflow.

## Interpreting results

| Result | Meaning / action |
| --- | --- |
| HTTP 202, queued | Signature/schema passed; minimized event persisted and background processing scheduled. Poll for its eventual state. |
| HTTP 200, duplicate | Existing event claimed; no extra scheduling for queued/processed duplicates. Failed records can be reclaimed under existing retry rules. |
| HTTP 401 | Missing/wrong signature or mismatched secret/raw bytes; event not trusted. |
| HTTP 422 | Authentic but structurally invalid payload; fix the producer/schema mismatch, not signature validation. |
| unresolved | Accepted event has no exact merchant account mapping; map and retry. |
| failed | Post-ack processing raised an error; inspect its safe exception category, correct the cause and retry. |
| manual_review | Missing/inaccessible payment, missing network/reason, unsupported rail/playbook, expired deadline or out-of-order first event requires a human. |
| scheduled / updated / stale / outcome_not_eligible | Existing workflow/lifecycle semantics; inspect dispute detail. None alone proves filing or a real adjudicated outcome. |

A missing sample payment typically causes enrichment degradation and manual
review, not HTTP rejection of an otherwise valid webhook. Historical examples
are overdue. Network reason codes are not guessed from provider reasons.
WON/LOST still require the filed invariant; CLOSED invents no outcome, and stale
events cannot regress established terminal state. The local filing stub makes no
provider accept/contest/upload request and its confirmation is not submission.

Razorpay documents a 2xx response window of five seconds, retries after failures,
and possible out-of-order delivery. Measure actual provider delivery latency;
TestClient waits for background tasks and cannot prove that latency. Existing
recovery uses persisted events plus in-process background work and a bounded
startup pass. It remains a single-process exercise, not a durable job queue.
Secret rotation currently has one configured verifier secret: outstanding retries
signed with an old secret need an explicit rotation plan.

## Offline verification and remaining evidence

On 2026-09-18, using `python -m poetry run python -m pytest`:

- Focused webhook/recovery/client/reconciliation/connector suite: **87 passed**.
- `git diff --check` passed.
- Full backend suite: **490 passed, 37 skipped**. The 37 identity/PostgreSQL tests
  require an explicitly isolated `CHARGEGUARD_TEST_DATABASE_URL`, which was not
  configured in this run. No database was provisioned or migrated.
- Full run emitted 36,528 warnings (existing deprecations); the Poetry launcher
  also reports a requests dependency-version warning. Tests ran under the available
  Python 3.14 environment; the Dockerfile targets Python 3.11.
- No frontend files changed. No real provider API requests, deployment,
  dashboard changes, secret-file access, commit or push were performed.

The first test run hit sandbox temporary-directory permissions and a test-only
datetime serialization assertion. Final runs used isolated workspace temporary
directories; fixture account format and retry HTTP expectations were corrected.

Actual HTTPS delivery, provider response-time evidence, account-specific dashboard
sample availability, Test-key verification/enrichment, Supabase staging login,
and retrieval of provider Test Mode payment/dispute IDs remain **unverified**.
Connectivity succeeds only with corresponding Razorpay delivery records and
ChargeGuard event records. Offline tests cannot establish production readiness.
