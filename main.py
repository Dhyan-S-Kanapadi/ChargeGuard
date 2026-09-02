from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.assistant import router as assistant_router
from api.disputes import router as disputes_router
from api.merchants import router as merchants_router
from api.razorpay_admin import (
    router as razorpay_admin_router,
    schedule_startup_razorpay_recovery,
)
from api.razorpay_simulator import router as razorpay_simulator_router
from api.razorpay_webhooks import router as razorpay_webhooks_router
from api.stats import router as stats_router
from api.webhooks import router as webhooks_router
from ml.model import WinProbabilityModel


logger = logging.getLogger(__name__)


def _log_deployment_warnings() -> None:
    if os.getenv("ENVIRONMENT", "development").strip().lower() != "production":
        return
    if not os.getenv("CHARGEGUARD_STORE_PATH", "").strip():
        logger.warning(
            "CHARGEGUARD_STORE_PATH is not configured in production; provider events "
            "and disputes will not survive a restart."
        )
    logger.warning(
        "The synchronized JSON store supports one application process only. "
        "Multi-worker production requires a shared transactional database and "
        "durable queue/outbox with atomic event claim and job creation."
    )


@asynccontextmanager
async def _lifespan(_: FastAPI):
    _log_deployment_warnings()
    schedule_startup_razorpay_recovery()
    yield


app = FastAPI(title="ChargeGuard AI", version="0.1.0", lifespan=_lifespan)
app.include_router(webhooks_router)
app.include_router(disputes_router)
app.include_router(merchants_router)
app.include_router(stats_router)
app.include_router(assistant_router)
app.include_router(razorpay_admin_router)
app.include_router(razorpay_webhooks_router)
app.include_router(razorpay_simulator_router)
app.mount("/dashboard", StaticFiles(directory="static", html=True), name="dashboard")


def _model_loaded() -> bool:
    artifact_path = Path(os.getenv("MODEL_PATH", "./ml/artifacts/win_probability_model.pkl"))
    if not artifact_path.is_file():
        return False
    try:
        WinProbabilityModel.load(artifact_path)
    except (OSError, TypeError, ValueError):
        return False
    return True


def _stub_mode() -> bool:
    return os.getenv("CHARGEGUARD_USE_STUBS", "").strip().lower() in {"1", "true", "yes", "on"}


@app.get("/health")
async def health() -> dict[str, str | bool]:
    model_loaded = _model_loaded()
    return {
        "status": "ok" if model_loaded else "degraded",
        "model_loaded": model_loaded,
        "stub_mode": _stub_mode(),
    }
