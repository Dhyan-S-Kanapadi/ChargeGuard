# ChargeGuard Hackathon Demo Runbook

This runbook is the stable five-minute presentation path. It uses deterministic provider stubs so the demo does not depend on third-party sandbox availability. It demonstrates real ChargeGuard orchestration, scoring, quality checks, and safe escalation. Razorpay dispute creation and card-network filing are not claimed as live.

## Before Recording Or Presenting

1. Confirm the deployed commit matches the latest successful `main` build.
2. Configure `ENVIRONMENT=production`, `CHARGEGUARD_USE_STUBS=true`, a private `API_KEY`, `RAZORPAY_WEBHOOK_ENABLED=true`, and `RAZORPAY_SIMULATOR_ENABLED=false`.
3. Open `https://<host>/health` and require `status=ok`, `model_loaded=true`, and `stub_mode=true`.
4. Open `https://<host>/dashboard/`, connect using the deployment API key, and keep the key outside the screen recording.
5. Warm the deployment five minutes before the presentation.
6. Run one complete rehearsal and record a backup video.

## Run The Deployed Workflow

PowerShell:

```powershell
$env:CHARGEGUARD_API_URL="https://<host>"
$env:API_KEY="<deployment-api-key>"
poetry run python scripts/demo.py
```

Bash:

```bash
CHARGEGUARD_API_URL=https://<host> \
API_KEY=<deployment-api-key> \
poetry run python scripts/demo.py
```

Each run creates unique merchant and dispute IDs. The command exits unsuccessfully if health is degraded or any case returns a decision other than the expected one.

## Five-Minute Sequence

### 0:00-0:35 — Problem

Merchant evidence is fragmented across payment, commerce, shipping, support, and fraud systems. Missing evidence or a filing deadline can turn a valid sale into a loss.

### 0:35-1:05 — Architecture

ChargeGuard receives a normalized internal event or signed Razorpay webhook. LangGraph coordinates evidence agents through one shared `ChargebackState`; agents do not independently message one another.

Evidence agents collect normalized facts, then deterministic ML and expected value produce the authoritative decision. When configured, an open-weight LLM independently reviews and explains that result. A disagreement is shown to the operator but cannot change routing or file the dispute; human review remains available. The LLM acts as the dispute analyst, while the deterministic decision engine acts as the financial risk controller.

### 1:05-2:35 — FIGHT

Run the demo and open the generated high-value case. Show transaction and delivery evidence, win probability, expected value, decision reasoning, quality approval, and the rebuttal path. State that the current filing confirmation is local and not a real Razorpay submission.

### 2:35-3:20 — ACCEPT

Open the low-value case. Explain that ChargeGuard does not fight every dispute: a case is accepted when its expected recovery does not justify response cost.

### 3:20-4:05 — ESCALATE_DEGRADED

Open the degraded case. Explain that missing required evidence never becomes a guessed decision; the case moves to human review.

### 4:05-4:35 — Webhook Safety

Describe or show the local signed simulator tests for valid signatures, invalid signatures, duplicates, unknown merchants, expired deadlines, and out-of-order events. The simulator is development-only and never contacts Razorpay.

### 4:35-5:00 — Failure Story And Roadmap

Razorpay Test Mode does not provide ChargeGuard with an on-demand public create-dispute operation. The project therefore added a signed Razorpay-shaped simulator that exercises the production webhook contract without bypassing signature, idempotency, or ordering checks. The next product layer is a separate UPI refund and reversal workflow with deterministic RBI/NPCI SLA policies.

## Claims Boundary

Safe claims:

- Deployed end-to-end decision and evidence-orchestration prototype.
- Deterministic FIGHT, ACCEPT, and human-escalation paths.
- Signed Razorpay-shaped webhook simulation with idempotency and ordering protection.
- Merchant-scoped encrypted Razorpay/Stripe and SEON connector infrastructure.
- Human-reviewed optional LLM classification assistance.
- Optional advisory open-weight LLM decision review that cannot change or file a dispute.

Do not claim:

- A real chargeback was created in Razorpay Test Mode.
- Provider evidence is live while `stub_mode=true`.
- The local filing confirmation submitted evidence to Razorpay or a card network.
- UPI refund disputes are already automated.
- The JSON store is ready for unattended multi-worker production.

## Fallback Order

1. Deployed dashboard and live API workflow.
2. Previously recorded five-minute backup video.
3. Screenshots of the three completed cases.
4. GitHub Actions result plus repository architecture walkthrough.

Never troubleshoot provider credentials or redeploy during the five-minute judging slot.
