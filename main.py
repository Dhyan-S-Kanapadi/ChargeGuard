from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api.assistant import router as assistant_router
from api.auth import require_api_key
from api.identity import router as identity_router
from core.identity import validate_identity_configuration
from core.runtime import provider_modes, runtime_environment, validate_runtime_environment
from core.runtime import RuntimeConfigurationError
from db.postgres import StoreConflictError
from api.demo_bootstrap import seed_demo_merchant
from api.public_demo import router as public_demo_router, validate_public_demo
from api.disputes import router as disputes_router
from api.device_risk_connectors import router as device_risk_connectors_router
from api.consortium_connectors import router as consortium_connectors_router
from api.merchants import router as merchants_router
from api.orders import router as orders_router
from api.payment_connectors import router as payment_connectors_router
from api.shipping_connectors import router as shipping_connectors_router
from api.razorpay_admin import (
    router as razorpay_admin_router,
    schedule_startup_razorpay_recovery,
)
from api.razorpay_simulator import router as razorpay_simulator_router
from api.razorpay_webhooks import router as razorpay_webhooks_router
from api.stats import router as stats_router
from api.webhooks import router as webhooks_router
from api.store import store
from ml.model import WinProbabilityModel


logger = logging.getLogger(__name__)


def _log_deployment_warnings() -> None:
    if os.getenv("ENVIRONMENT", "development").strip().lower() != "production":
        return
    if not hasattr(store, "check_ready") and not os.getenv("CHARGEGUARD_STORE_PATH", "").strip():
        logger.warning(
            "CHARGEGUARD_STORE_PATH is not configured in production; provider events "
            "and disputes will not survive a restart."
        )
    if not os.getenv("CHARGEGUARD_CREDENTIAL_ENCRYPTION_KEY", "").strip():
        logger.warning(
            "CHARGEGUARD_CREDENTIAL_ENCRYPTION_KEY is not configured; "
            "merchant provider connectors will fail closed."
        )
    if not os.getenv("CHARGEGUARD_CREDENTIAL_STORE_PATH", "").strip():
        logger.warning(
            "CHARGEGUARD_CREDENTIAL_STORE_PATH is not configured; "
            "merchant provider connectors will fail closed."
        )
    if os.getenv("ALLOW_GLOBAL_PAYMENT_CREDENTIAL_FALLBACK", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }:
        logger.warning("Global payment credential fallback is enabled in production.")
    if os.getenv("ALLOW_GLOBAL_SEON_CREDENTIAL_FALLBACK", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }:
        logger.warning("Global SEON credential fallback is enabled in production.")
    if hasattr(store, "check_ready"):
        logger.warning("PostgreSQL is configured; managed credentials and durable jobs are still required before multi-worker deployment.")
    else:
        logger.warning(
            "The synchronized storage supports one application process only, including "
            "the JSON and encrypted credential files. "
            "Multi-worker production requires a shared transactional database and "
            "durable queue/outbox with atomic event claim and job creation."
        )


@asynccontextmanager
async def _lifespan(_: FastAPI):
    validate_runtime_environment()
    validate_identity_configuration()
    if hasattr(store, "check_ready"):
        store.check_ready()
    _log_deployment_warnings()
    seed_demo_merchant()
    validate_public_demo()
    schedule_startup_razorpay_recovery()
    yield


app = FastAPI(title="ChargeGuard AI", version="0.1.0", lifespan=_lifespan)


@app.exception_handler(StoreConflictError)
async def store_conflict_handler(request: Request, exc: StoreConflictError):
    return JSONResponse(status_code=409, content={"detail": "Store conflict; reload the resource before retrying."})


@app.exception_handler(RuntimeConfigurationError)
async def runtime_error_handler(request: Request, exc: RuntimeConfigurationError):
    return JSONResponse(status_code=503, content={"detail": "Required runtime capability is unavailable."})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
):
    if "/device-risk-connectors" in request.url.path:
        return JSONResponse(
            status_code=422,
            content={"detail": "invalid_device_risk_connector_request"},
        )
    if "/payment-connectors" in request.url.path:
        return JSONResponse(
            status_code=422,
            content={"detail": "invalid_payment_connector_request"},
        )
    if "/shipping-connectors" in request.url.path:
        return JSONResponse(
            status_code=422,
            content={"detail": "invalid_shipping_connector_request"},
        )
    if "/consortium-connectors" in request.url.path:
        return JSONResponse(
            status_code=422,
            content={"detail": "invalid_consortium_connector_request"},
        )
    return await request_validation_exception_handler(request, exc)


app.include_router(webhooks_router)
app.include_router(identity_router)
app.include_router(disputes_router)
app.include_router(merchants_router)
app.include_router(orders_router)
app.include_router(payment_connectors_router)
app.include_router(shipping_connectors_router)
app.include_router(consortium_connectors_router)
app.include_router(device_risk_connectors_router)
app.include_router(stats_router)
app.include_router(assistant_router)
app.include_router(public_demo_router)
app.include_router(razorpay_admin_router)
app.include_router(razorpay_webhooks_router)
app.include_router(razorpay_simulator_router)


def _dashboard_directory() -> Path:
    root = Path(__file__).resolve().parent
    frontend_build = root / "frontend" / "dist"
    if (frontend_build.joinpath("index.html").is_file()):
        return frontend_build
    return root / "static"


app.mount("/dashboard", StaticFiles(directory=_dashboard_directory(), html=True), name="dashboard")


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


@app.get("/internal/runtime", dependencies=[Depends(require_api_key)])
async def runtime_status() -> dict:
    return {
        "environment": runtime_environment(),
        "evidence_providers": provider_modes(),
        "production_ready": False,
        "submission": "local_stub_only_outside_production",
        "remaining_prerequisites": ["durable_jobs", "live_identity_setup_verification",
                                    "private_versioned_artifacts", "verified_human_submission"],
    }
