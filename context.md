# ChargeGuard Project Context

## Current Repository State

- Local repo path: `D:\ChargeGuard\chargeguard`
- GitHub remote: `https://github.com/Dhyan-S-Kanapadi/ChargeGuard.git`
- Current branch: `feature/langgraph-agent-refactor`
- Main working branch for active agent refactor: `feature/langgraph-agent-refactor`
- Integration branch: `develop`
- Stable branch: `main`

## Branches Created

- `main` - stable project branch.
- `develop` - integration branch for active development.
- `feature/langgraph-agent-refactor` - current branch for converting agents into LangGraph-compatible nodes.

## Git History So Far

- `7280935 Initial ChargeGuard project setup`
- `ca9a44c Refactor evidence agents`

The feature branch and develop branch are currently pushed to GitHub. The repository was initialized locally, linked to GitHub, and pushed after resolving GitHub's pre-existing `README.md` on `main`.

## Project Purpose

ChargeGuard is an autonomous chargeback dispute management system for Indian merchants. The intended system investigates chargebacks, collects evidence, scores dispute win probability, builds rebuttal documents, performs quality checks, files responses, and records outcomes for future learning.

## Tech Stack

- Python 3.11 target in `pyproject.toml`
- FastAPI for API surface
- LangGraph for agent orchestration
- ReportLab planned for rebuttal PDFs
- Neo4j planned for graph/customer/fraud history
- scikit-learn/XGBoost planned for ML scoring
- External provider integrations planned:
  - Razorpay
  - Shiprocket
  - Freshdesk
  - SEON
  - Ethoca
  - Verifi
  - Stripe
  - Delhivery

## Important Files

- `core/state.py` - central `ChargebackState` and evidence typed dictionaries.
- `core/graph.py` - LangGraph workflow definition.
- `core/enums.py` - enum definitions for networks, decisions, outcomes, verticals, and reason codes.
- `main.py` - minimal FastAPI app with `/health`.
- `branches.md` - branch plan for the project.
- `.gitignore` - Python/project ignore rules.

## Current LangGraph Flow

The graph now uses explicit agent nodes:

1. `orchestrator`
2. `transaction_evidence`
3. `shipping_evidence`
4. `device_evidence`
5. `comms_evidence`
6. `consortium_evidence`
7. conditional food path:
   - if food/quick-commerce evidence is required:
     - `delivery_photo_evidence`
     - `order_timeline_evidence`
   - otherwise go directly to scoring
8. `scoring`
9. conditional decision:
   - `FIGHT` -> `rebuttal_builder`
   - `ACCEPT` -> `accept_and_log`
10. `quality_check`
11. conditional quality route:
   - approved -> `filing`
   - retry -> `rebuttal_builder`
   - too many retries -> `human_escalation`
12. `learning`
13. `END`

## Agents Implemented or Refactored

### Orchestrator Agent

File: `agents/orchestrator.py`

- Creates `investigation_plan`.
- Detects whether food-specific agents are required.
- Uses merchant vertical and reason code.
- Adds deadline priority and days until deadline.

### Transaction Evidence Agent

File: `agents/evidence/transaction.py`

- Already had meaningful implementation.
- Builds transaction evidence from stubbed Razorpay-style payment/order responses.
- Populates OTP, 3DS, device ID, IP address, customer email, order history, and previous chargebacks.

### Shipping Evidence Agent

File: `agents/evidence/shipping.py`

- Already had meaningful implementation.
- Builds shipping evidence from stubbed Shiprocket-style tracking response.
- Populates delivery status, delivered timestamp, location, signature, and delivery photo URL.

### Device Evidence Agent

File: `agents/evidence/device.py`

- Replaced old alias with a real LangGraph node.
- Populates fraud score, device fingerprint, geolocation match, normal login pattern, VPN detection, and raw IP metadata.

### Comms Evidence Agent

File: `agents/evidence/comms.py`

- Replaced old alias with a real LangGraph node.
- Populates email evidence, support-ticket placeholder data, post-delivery interaction flag, complaint-before-chargeback flag, and Freshdesk metadata.

### Consortium Evidence Agent

File: `agents/evidence/consortium.py`

- Replaced old alias with a real LangGraph node.
- Populates Ethoca/Verifi match flags, cross-merchant fraud history, dispute count, and network metadata.

### Delivery Photo Evidence Agent

File: `agents/evidence/delivery_photo.py`

- Replaced old alias with a real LangGraph node.
- Uses shipping delivery photo URL.
- Populates AI verification, address visibility, timestamp, and raw shipping status.

### Order Timeline Evidence Agent

File: `agents/evidence/order_timeline.py`

- Replaced old alias with a real LangGraph node.
- Builds placed, accepted, picked, delivered timestamps, post-delivery rating, and raw order metadata.

### Scoring Agent

File: `agents/scoring.py`

- Converts evidence into deterministic win probability.
- Calculates expected value.
- Sets `decision` to `FIGHT` or `ACCEPT`.
- Writes `decision_reasoning`.

### Rebuttal Builder Agent

File: `agents/rebuttal_builder.py`

- Builds a deterministic JSON rebuttal packet.
- Writes output under `REBUTTAL_OUTPUT_DIR` or `./output/rebuttals`.
- Sets `rebuttal_document_path`.

### Quality Check Agent

File: `agents/quality_check.py`

- Checks that rebuttal document exists.
- Checks required evidence.
- Increments `quality_loop_count`.
- Sets approval/rejection fields.

### Filing Agent

File: `agents/filing.py`

- Records `filed_at`.
- Creates deterministic filing confirmation.

### Learning Agent

File: `agents/learning.py`

- Sets pending outcome defaults.
- Records `outcome_recorded_at`.

## Verification Performed

`pytest` was not installed in the environment, so full test execution could not be run with `python -m pytest`.

Lightweight checks completed successfully:

- Python compile check passed for changed graph/evidence files.
- `core.graph.app` imported successfully as `CompiledStateGraph`.
- Smoke run completed successfully.

Smoke result:

```text
FIGHT True True True True True True True True filed_visa_cb_smoke_001
```

This means:

- decision: `FIGHT`
- food agents required: `True`
- transaction evidence populated
- shipping evidence populated
- device evidence populated
- comms evidence populated
- consortium evidence populated
- delivery photo evidence populated
- order timeline evidence populated
- filing confirmation produced

## Known Environment Notes

- The environment uses Python 3.14 locally, while `pyproject.toml` targets Python 3.11.
- LangChain emitted a warning that Pydantic V1 compatibility is not ideal on Python 3.14.
- `pytest` was not installed locally when checked.
- Git initially reported dubious ownership because the repo was initialized by the sandbox user. The fix was:

```powershell
git config --global --add safe.directory D:/ChargeGuard/chargeguard
```

## Current Development Direction

Continue on:

```powershell
git checkout feature/langgraph-agent-refactor
```

Recommended next steps:

1. Refine each evidence agent one by one with real business logic.
2. Start with `device`, `comms`, `consortium`, `delivery_photo`, or `order_timeline`.
3. Replace stubbed evidence sources with integration clients after the agent contracts are stable.
4. Add tests for each agent as soon as its logic becomes more than deterministic placeholder behavior.
5. Merge `feature/langgraph-agent-refactor` into `develop` once the LangGraph refactor is stable.

## Common Git Commands

Check branch:

```powershell
git -C D:/ChargeGuard/chargeguard branch
```

Commit current work:

```powershell
git -C D:/ChargeGuard/chargeguard add .
git -C D:/ChargeGuard/chargeguard commit -m "Your commit message"
```

Push feature branch:

```powershell
git -C D:/ChargeGuard/chargeguard push origin feature/langgraph-agent-refactor
```

Merge feature into develop:

```powershell
git -C D:/ChargeGuard/chargeguard checkout develop
git -C D:/ChargeGuard/chargeguard pull origin develop
git -C D:/ChargeGuard/chargeguard merge feature/langgraph-agent-refactor
git -C D:/ChargeGuard/chargeguard push origin develop
git -C D:/ChargeGuard/chargeguard checkout feature/langgraph-agent-refactor
```
