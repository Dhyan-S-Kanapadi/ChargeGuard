# Reviewer demo: synthetic disputes with live Guard AI

This deployment is an isolated simulation, not a production payment handler.
All payment, shipping, device/IP, support, consortium and delivery-photo evidence is
synthetic. Guard AI and the advisory decision reviewer can make real Groq calls.
Neither LLM can override the deterministic FIGHT/ACCEPT/ESCALATE decision.
Filing remains a local confirmation stub; simulated outcomes never train the real model.

## Deploy on Render

Use **New > Blueprint**, select this repository and the branch containing
`render.yaml`, and review the proposed free web service before deploying.
The Blueprint belongs at the repository root (alongside the Dockerfile).
For an existing service, apply the same environment settings manually; adding this
file does not automatically reconfigure a service that was created manually.

Paste Groq keys into these **server-side Render environment variables**:

| Feature | Secret variable |
| --- | --- |
| Guard AI dashboard chat | `PORTFOLIO_ASSISTANT_API_KEY` |
| Advisory second review | `LLM_DECISION_REVIEW_API_KEY` |

One Groq key may be used for both, but calls share the account's quota.
Do not put Groq keys in the dashboard, frontend build variables, Git, or screenshots.
The Blueprint supplies the Groq URL, model, low reasoning effort, and token budget.
It generates a separate `API_KEY` for dashboard access and a webhook signing secret.
For local use, paste the keys in ignored `.env.local`, not a tracked file.

The configured model must be available to your Groq account. Provider quotas,
outages or rejected requests can make AI unavailable; this is not silently
replaced with a fake live answer. Guard AI returns an unavailable error; case
reviews store a safe unavailable code and automation continues deterministically.

## What to give the reviewer

1. The service's `https://YOUR-SERVICE.onrender.com/dashboard/` URL.
2. The generated dashboard `API_KEY`, sent privately.
3. Instructions below. The reviewer does **not** need your Groq keys.

The API key grants operator access to this shared demo. Only invite trusted
reviewers; never reuse this key or demo service for real merchants or customer data.

## Five-minute walkthrough

1. Open the dashboard. Set the API origin to the same Render service URL and paste
   the dashboard API key. A sleeping free service may take time to start.
2. Open **Simulator** and select **Reviewer Demo Merchant**. Startup seeds this
   synthetic merchant automatically; do not connect real payment credentials.
3. Run one catalog case at a time. The 32 cases cover eight families with four
   examples each, including different amounts, partial disputes, device/IP risk,
   provider failures, webhook rejection, lifecycle ordering and manual-review routes.
4. Wait for processing, then open the generated case in **Disputes**. Inspect
   evidence, deterministic decision, degradation and the advisory LLM review.
   Rejected signatures deliberately create no dispute; unresolved accounts
   deliberately wait for mapping. These are successful negative tests.
5. Open **Guard AI**. Its status panel distinguishes live-configured, stub,
   disabled and missing configuration. Paste the generated dispute ID in Optional
   chargeback context and ask: "Explain this synthetic case and its device-risk signals."
6. A completed review or an actual chat answer proves a live call succeeded;
   "Live LLM configured" alone only proves configuration is present.

Avoid rapidly running all 32 cases with live reviews enabled on a free Groq account.
For a bulk deterministic test, temporarily set
`LLM_DECISION_REVIEW_USE_STUBS=true`, then restore `false` and run one live case.
See [SIMULATION_TEST_MATRIX.md](SIMULATION_TEST_MATRIX.md) for expected outcomes.

## Verified local checks

The Docker app passed all 32 catalog scenarios in a clean, automatically seeded
demo environment: 17 FIGHT, 1 ACCEPT, 12 ESCALATE_DEGRADED, 1 intentionally
rejected signature, and 1 unresolved account. Bulk runs used stub LLM reviews.
Separate live Groq checks returned both a Guard AI answer and a schema-valid
completed decision review on a synthetic VPN/geolocation-mismatch case.
These checks demonstrate the tested paths, not exhaustive real-world coverage
or a guarantee of future provider availability.

## Data and restart limits

The free Blueprint stores demo data under `/tmp/chargeguard`. Records and PDFs may
disappear after restart or redeploy. The demo merchant is recreated automatically;
reviewers can run the catalog again. This is deliberate demo-only storage, not
durable production persistence. Local Docker Compose uses a named data volume.
Production needs durable storage/queueing, real provider validation and real filing.

## Local checks

```powershell
docker compose --env-file .env.local up --build -d
python -m pytest -q
cd frontend
npm test
npm run lint
npm run build
```

Use the configured local port (currently 8200) at
`http://127.0.0.1:8200/dashboard/`.
The catalog runner `python scripts/run_simulation_catalog.py --help` describes
its loopback-only usage; it does not target a deployed service.

Deployment reference: [Render Blueprints](https://render.com/docs/infrastructure-as-code).
Free-service limits: [Render free instances](https://render.com/docs/free).
