"""Opt-in, session-scoped public demo. Operator authentication is unchanged."""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import secrets
import sqlite3
from threading import BoundedSemaphore
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from api.assistant import _summary_from_record, llm_status
from api.demo_bootstrap import seed_demo_merchant
from api.disputes import _redact_state
from api.razorpay_simulator import execute_scenario, _safe_record
from api.schemas import AssistantQuery, AssistantResponse, DisputeDetail, DisputeSummary, RazorpaySimulatorScenarioRun
from api.simulation_scenarios import get_simulation_scenario, list_simulation_scenarios
from api.stats import build_stats
from api.store import store
from integrations.portfolio_assistant import generate_portfolio_answer

router = APIRouter(prefix="/demo", tags=["public-demo"])
MERCHANT_ID = "merchant_reviewer_demo"
SESSION_SECONDS = 3600
_work_slots = BoundedSemaphore(2)


def _flag(name):
    return os.getenv(name, "").strip().lower() in {"true", "1", "yes", "on"}


def public_demo_enabled():
    return _flag("PUBLIC_DEMO_ENABLED")


def validate_public_demo():
    if not public_demo_enabled():
        return
    if not _flag("CHARGEGUARD_DEMO_SEED"):
        raise RuntimeError("Public demo requires synthetic merchant seeding")
    seed_demo_merchant()  # Includes production and live-evidence guards.
    merchant = store.get_merchant(MERCHANT_ID)
    if (merchant.get("razorpay_account_id") != "acc_REVIEWERDEMO"
            or merchant.get("payment_provider") != "razorpay"
            or any(merchant.get(key) for key in (
                "payment_connector_id", "payment_connector_ids", "device_risk_connector_id",
                "support_connector_ref", "shopify_admin_api_token", "woocommerce_api_key",
                "woocommerce_api_secret", "gmail_user_id", "freshdesk_domain"))):
        raise RuntimeError("Public demo merchant must remain synthetic and unconnected")
    if (not _flag("RAZORPAY_WEBHOOK_ENABLED") or not os.getenv("RAZORPAY_WEBHOOK_SECRET")
            or not _flag("CASE_SUMMARY_USE_STUBS")
            or _flag("REBUTTAL_NARRATIVE_ENABLED") or _flag("REASON_CLASSIFICATION_ENABLED")):
        raise RuntimeError("Public demo requires signed simulation and no additional live AI features")


def _require_enabled():
    if not public_demo_enabled():
        raise HTTPException(404, "Public demo is unavailable.")
    try:
        validate_public_demo()
    except RuntimeError as exc:
        raise HTTPException(503, "Public demo is not safely configured.") from exc


@contextmanager
def _db():
    # Single-process demo; SQLite persists atomic budgets on the configured filesystem.
    # A public multi-instance service needs shared quotas/session storage.
    path = Path(os.getenv("PUBLIC_DEMO_DB_PATH", "./data/public_demo.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY, peer TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS cases (
                id TEXT PRIMARY KEY, session TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage (
                day INTEGER, bucket TEXT, kind TEXT, used INTEGER NOT NULL,
                PRIMARY KEY(day, bucket, kind));
        """)
        with connection:
            yield connection
    finally:
        connection.close()


def _reserve(connection, charges):
    """Reserve before work; failures consume quota too. Never log tokens or peers."""
    day = int(time.time() // 86400)
    connection.execute("BEGIN IMMEDIATE")
    for bucket, kind, amount, limit in charges:
        row = connection.execute(
            "SELECT used FROM usage WHERE day=? AND bucket=? AND kind=?",
            (day, bucket, kind),
        ).fetchone()
        if (row["used"] if row else 0) + amount > limit:
            raise HTTPException(429, "Demo limit reached. Try again tomorrow.",
                                headers={"Retry-After": str(max(1, int((day + 1) * 86400 - time.time())))})
    for bucket, kind, amount, _ in charges:
        connection.execute(
            "INSERT INTO usage VALUES (?,?,?,?) ON CONFLICT(day,bucket,kind) "
            "DO UPDATE SET used=used+excluded.used", (day, bucket, kind, amount))


def _limit(name, default):
    try:
        return max(0, min(1000, int(os.getenv(name, str(default)))))
    except ValueError:
        return 0  # Invalid configuration never removes the quota.


@router.get("/status")
def demo_status():
    return {"enabled": public_demo_enabled(), "session_minutes": 60,
            "runs_per_session": 8, "chats_per_session": 8}


@router.post("/session")
def start_session(request: Request, response: Response):
    _require_enabled()
    if request.headers.get("X-Demo-Request") != "1":
        raise HTTPException(403, "Explicit demo request required.")
    # Do not trust arbitrary X-Forwarded-For headers. Proxy peers may share limits.
    peer = hashlib.sha256((request.client.host if request.client else "unknown").encode()).hexdigest()
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = time.time()
    with _db() as connection:
        _reserve(connection, [("global", "sessions", 1, 100), (peer, "sessions", 1, 5)])
        connection.execute("DELETE FROM cases WHERE session IN (SELECT token FROM sessions WHERE expires<?)", (now,))
        connection.execute("DELETE FROM sessions WHERE expires<?", (now,))
        connection.execute("DELETE FROM usage WHERE day<?", (int(now // 86400) - 1,))
        connection.execute("INSERT INTO sessions VALUES (?,?,?)", (token_hash, peer, now + SESSION_SECONDS))
    response.headers["Cache-Control"] = "no-store"
    return {"session_token": token, "expires_in": SESSION_SECONDS}


def require_session(response: Response, x_demo_session: str | None = Header(default=None)):
    _require_enabled()
    if not x_demo_session or len(x_demo_session) > 100:
        raise HTTPException(401, "Start a demo session first.")
    token_hash = hashlib.sha256(x_demo_session.encode()).hexdigest()
    with _db() as connection:
        session = connection.execute("SELECT * FROM sessions WHERE token=? AND expires>?",
                                     (token_hash, time.time())).fetchone()
        if session is None:
            raise HTTPException(401, "Demo session expired. Disconnect and try the demo again.")
        _reserve(connection, [(token_hash, "reads", 1, 2000)])
    response.headers["Cache-Control"] = "no-store"
    return dict(session)


def _records(session):
    with _db() as connection:
        ids = [row["id"] for row in connection.execute("SELECT id FROM cases WHERE session=?", (session["token"],))]
    return [record for case_id in ids if (record := store.get_dispute(case_id)) is not None
            and case_id.startswith("disp_SIM_")
            and record["state"].get("merchant_profile", {}).get("merchant_id") == MERCHANT_ID]


def _owned_record(session, case_id):
    record = next((record for record in _records(session) if record["chargeback_id"] == case_id), None)
    if record is None:
        raise HTTPException(404, "Demo dispute not found.")
    return record


@contextmanager
def _reserve_work(session, kind):
    if not _work_slots.acquire(blocking=False):
        raise HTTPException(429, "Demo is busy. Retry shortly.", headers={"Retry-After": "10"})
    try:
        # A graph run can make at most two decision-review requests (one retry).
        # Other graph LLMs are stubbed or disabled by validate_public_demo.
        with _db() as connection:
            _reserve(connection, [
                (session["token"], kind, 1, 8), (session["peer"], kind, 1, 16),
                ("global", "llm", 2 if kind == "runs" else 1, _limit("PUBLIC_DEMO_DAILY_LLM_BUDGET", 100)),
                ("global", kind, 1, _limit("PUBLIC_DEMO_DAILY_RUN_LIMIT", 40) if kind == "runs" else 60),
            ])
        yield
    finally:
        _work_slots.release()


@router.get("/merchants")
def demo_merchants(session=Depends(require_session)):
    # Fixed synthetic projection, never expose operator merchant profiles.
    return [{"merchant_id": MERCHANT_ID, "name": "Reviewer Demo Merchant",
             "vertical": "ecommerce", "payment_provider": "razorpay",
             "razorpay_account_id": "acc_REVIEWERDEMO", "freshdesk_domain": "",
             "average_order_value": 3799, "chargeback_history_count": 0,
             "transaction_volume_30d_by_network": {}, "merchant_dispute_ratio": {},
             "storefront_platform": "custom", "platform_credential_verified": False}]


@router.get("/stats")
def demo_stats(session=Depends(require_session)):
    return build_stats(_records(session))


@router.get("/disputes", response_model=list[DisputeSummary])
def demo_disputes(session=Depends(require_session)):
    return [DisputeSummary(
        **{key: record[key] for key in ("chargeback_id", "status", "created_at", "updated_at")},
        decision=record["state"].get("decision"), dispute_amount=record["state"]["dispute_amount"],
        currency=record["state"]["currency"], merchant_id=MERCHANT_ID,
    ) for record in _records(session)]


@router.get("/disputes/{case_id}", response_model=DisputeDetail)
def demo_dispute(case_id: str, request: Request, session=Depends(require_session)):
    if request.query_params:  # No raw-detail bypass, even with an internal token.
        raise HTTPException(403, "Raw evidence is unavailable in public demo.")
    record = _owned_record(session, case_id)
    return DisputeDetail(**{**record, "state": _redact_state(record["state"])})


@router.get("/assistant/status")
def demo_llm_status(session=Depends(require_session)):
    return llm_status()


@router.post("/assistant/query", response_model=AssistantResponse)
def demo_chat(payload: AssistantQuery, session=Depends(require_session)):
    records = ([_owned_record(session, payload.chargeback_id)] if payload.chargeback_id else _records(session))
    context = {"stats": build_stats(records), "disputes": [_summary_from_record(record) for record in records[:8]]}
    if payload.chargeback_id:
        context.update(requested_chargeback_id=payload.chargeback_id, requested_chargeback_found=True)
    with _reserve_work(session, "chats"):
        try:
            answer = generate_portfolio_answer(payload.question, context)
        except Exception as exc:
            raise HTTPException(503, "Demo AI is temporarily unavailable.") from exc
    return AssistantResponse(answer=answer, based_on={"dispute_count": len(context["disputes"]), "stats_snapshot": True})


@router.get("/dev/razorpay-simulator/scenarios")
def demo_scenarios(session=Depends(require_session)):
    return list_simulation_scenarios()


@router.post("/dev/razorpay-simulator/scenarios/{scenario_id}/run")
def demo_run(scenario_id: str, payload: RazorpaySimulatorScenarioRun, session=Depends(require_session)):
    if payload.merchant_id != MERCHANT_ID:
        raise HTTPException(403, "Only the synthetic demo merchant is available.")
    if get_simulation_scenario(scenario_id) is None:
        raise HTTPException(404, "Scenario not found.")
    def claim(case_id):
        with _db() as connection:
            connection.execute("INSERT INTO cases VALUES (?,?)", (case_id, session["token"]))
    with _reserve_work(session, "runs"):
        result = execute_scenario(scenario_id, payload, on_created=claim)
    # Do not return simulator HMAC signatures or internal delivery response bodies.
    return {**result, "deliveries": [
        {key: delivery[key] for key in ("event_id", "event_name", "payload_sha256")} |
        {"delivery": {"status_code": delivery["delivery"]["status_code"]}}
        for delivery in result["deliveries"]
    ]}


@router.get("/dev/razorpay-simulator/disputes")
def demo_simulations(session=Depends(require_session)):
    with _db() as connection:
        ids = [row["id"] for row in connection.execute("SELECT id FROM cases WHERE session=?", (session["token"],))]
    records = [_safe_record(record) for case_id in ids if (record := store.get_simulator_dispute(case_id))]
    for record in records:
        record["deliveries"] = []  # Internal signatures never reach a public session.
    return records
