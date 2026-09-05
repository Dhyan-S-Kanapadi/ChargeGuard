# ChargeGuard Simulation and Test Matrix

## Readiness verdict

ChargeGuard is ready for **local, single-process simulation** of its implemented control flow. It is not a production card-network filing system: the filing step writes a local confirmation only, Razorpay dispute creation is simulated, global provider stubs return deterministic synthetic evidence, and multi-worker deployment still requires a transactional database plus durable queue/outbox.

The simulator never calls Razorpay. It creates only `disp_SIM_*` records, seeds a merchant-owned synthetic order, signs the exact JSON body, and posts only to a loopback `/webhook/razorpay` endpoint. Production mode always returns 404 for simulator routes.

## Runnable dashboard catalog

Open **Dashboard → Simulator**, choose a mapped Razorpay merchant, select an example, and click **Run selected scenario**. Every family contains four examples.

| Family | Four runnable examples |
|---|---|
| Decision routing | High-value delivered friendly-fraud case → FIGHT; low-value delivered case → ACCEPT; UPI in a card workflow → human review; expired response window → human review |
| Network playbooks | Visa 10.4 card-absent fraud; Visa 13.3 not-as-described; Mastercard 4853 cardholder dispute; RuPay UA02 unauthorized CNP |
| Payment rails | Card is eligible; UPI remains UPI; netbanking remains NETBANKING; wallet remains WALLET |
| Webhook trust | Valid exact-body signature; invalid signature; identical event retry; valid event for an unknown account |
| Provider lifecycle | Created → action required; created → under review; WON before CREATED; created → closed |
| Automation boundaries | Missing mapped network reason; unsupported Amex playbook; partial-amount preservation; urgent future deadline |

Two additional runnable families cover device evidence (32 cases total):

| Family | Four runnable examples |
|---|---|
| Device and IP | Consistent device/location (risk 8); new mobile device (22); VPN/location mismatch (91); travelling customer with IPv6 (35) |
| Device failures | Timeout; missing IP/fingerprint; malformed score; authentication failure |

Every case has a distinct disputed amount, from INR 349 to INR 31,499. The partial-dispute example disputes INR 12,500 of an INR 50,000 payment; both amounts are shown in the dashboard.
Device fixtures are selected from locally stored, merchant/payment-matched simulator records only when the simulator and provider stubs are enabled outside production. They exercise the same normalization and scoring functions as live evidence. No real connector is changed by a simulated failure. All `disp_SIM_*` outcomes are excluded from training.

The catalog API is `GET /dev/razorpay-simulator/scenarios`. Run one example with:

```http
POST /dev/razorpay-simulator/scenarios/{scenario_id}/run
X-API-Key: <development API key>
Content-Type: application/json

{"merchant_id":"merchant_001"}
```

Each run generates new payment/order/dispute IDs. Reusing manual payment or order IDs is rejected with 409 so two simulated cases cannot silently share commerce evidence.

To load and verify all 32 examples against the running local demo, set `API_KEY` in the shell and run:

```powershell
py scripts/run_simulation_catalog.py --base-url http://127.0.0.1:8200 --merchant merchant_demo
```

The runner waits for the final lifecycle event and completed workflow, checks stored amounts, device signals, routing, and filing invariants, prints PASS/FAIL per example, and exits nonzero on failure. Existing cases are retained.

## Complete implemented-control test matrix

The runnable catalog covers end-to-end event shapes. Provider failures, privacy boundaries, concurrency, and model behavior are exercised deterministically by automated tests. Each family below has four representative real-world examples.

| Family | Example 1 | Example 2 | Example 3 | Example 4 | Primary automated coverage |
|---|---|---|---|---|---|
| Merchant and connector ownership | Valid Razorpay connector | Invalid credential | Cross-merchant access | Safe credential rotation | `test_payment_connectors.py` |
| Storefront/order foundation | Verified Shopify token | Invalid WooCommerce credential | Paginated history with 429 | Duplicate provider ID conflict | `test_storefront_credentials.py`, `test_shopify_sync.py`, `test_orders_api.py` |
| Ingress authentication | Missing API key | Valid raw-body HMAC | Changed body after signing | Oversized webhook body | `test_api.py`, `test_razorpay_webhooks.py` |
| Idempotency and merchant mapping | Repeated event ID | Digest fallback without ID | Unknown provider account | Same create cannot schedule twice | `test_razorpay_webhooks.py`, `test_razorpay_event_recovery.py` |
| Order correlation | Provider payment ID match | Provider order ID match | Verified commerce reference | Cross-merchant/fuzzy match rejected | `test_order_correlation.py` |
| Transaction and shipping evidence | Razorpay normalization | Stripe normalization | Shiprocket success | Delhivery fallback after Shiprocket failure | `test_transaction_agent.py`, `test_shipping_agent.py` |
| Device/comms/consortium evidence | SEON success | Missing SEON credential | Gmail fails but Freshdesk succeeds | Verifi fails but Ethoca succeeds | `test_device_agent.py`, `test_comms_agent.py`, `test_consortium_agent.py` |
| Food-delivery evidence | Existing proof-of-delivery photo | Food adapter photo | Claude Vision verification | Photo/timeline provider failure | `test_delivery_photo_agent.py`, `test_order_timeline_agent.py` |
| Visa CE3.0 | Two qualifying prior orders | Only one prior order | Orders outside 120–365-day window | Prior disputed/fraud-flagged orders excluded | `test_purchase_history.py`, `test_ce3_wiring.py` |
| Deterministic decisions | Friendly fraud → FIGHT | Genuine non-delivery → ACCEPT | Stolen-card facts → ACCEPT | Evidence/model failure → ESCALATE_DEGRADED | `test_decision_scenarios.py`, `test_scoring.py` |
| Advisory LLM safety | LLM disabled | Valid agreement | Disagreement cannot reroute | Timeout/malformed/provider error fails safely | `test_decision_review.py` |
| Rebuttal and quality | Valid PDF approved | Missing required evidence | Prohibited language auto-fixed | Three failures escalate | `test_pdf_builder.py`, `test_quality_check_agent.py` |
| Filing and adjudication | Approved local filing | Missing PDF blocks filing | Filed WIN records feedback | ACCEPT/unfiled LOSS cannot train | `test_filing_agent.py`, `test_learning_agent.py`, `test_feedback.py` |
| Provider ordering | Action required then review | CLOSED adds no outcome | Conflicting terminal event ignored | Older non-terminal event cannot regress | `test_razorpay_webhooks.py` |
| Recovery and reconciliation | Failed event retry | Stale processing reclaim | Unknown account resolves after mapping | Reconciliation continues after one bad item | `test_razorpay_event_recovery.py`, `test_razorpay_reconciliation.py` |
| API privacy and assistant | Default raw evidence redaction | Internal-token raw access | Bounded case summary | Assistant receives at most 50 redacted cases | `test_api.py`, `test_case_summary.py`, `test_portfolio_assistant.py` |

## Local execution

Use Python 3.11, which is the version declared by the project. Configure development-only values without real provider credentials:

```env
ENVIRONMENT=development
API_KEY=<local-test-key>
CHARGEGUARD_USE_STUBS=true
RAZORPAY_WEBHOOK_ENABLED=true
RAZORPAY_WEBHOOK_SECRET=<local-simulator-secret>
RAZORPAY_SIMULATOR_ENABLED=true
RAZORPAY_SIMULATOR_TARGET_URL=http://127.0.0.1:8000/webhook/razorpay
```

Then train the deterministic baseline model, start one API process, and open the dashboard:

```powershell
py -m ml.train
py -m uvicorn main:app --port 8000
```

Run verification separately:

```powershell
py -m pytest -q
cd frontend
npm test -- --run
npm run lint
npm run build
```

Expected local safety properties:

- `GET /health` reports `model_loaded=true` and `stub_mode=true` before decision examples are trusted.
- Valid simulated creates return HTTP 202 from the receiver; invalid signatures return 401; exact duplicates return 200 with `status=duplicate`.
- A supported created event appears in `/disputes/{disp_SIM_id}` after background processing.
- FIGHT is not WIN, ACCEPT is not LOSS, and no outcome becomes a learning label unless the filed invariant and real WIN/LOSS gate both pass.

## What simulation cannot certify

- Real Razorpay/card-network dispute creation or representment submission.
- Third-party sandbox availability, credentials, rate limits, or payload drift.
- Production concurrency across multiple API workers.
- Regulatory, scheme-rule, or reason-code completeness beyond the checked-in playbooks.
- Every possible merchant data shape. The matrix is exhaustive for the implemented branches and safety boundaries, not for every event a provider could invent.
