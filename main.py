from fastapi import FastAPI

from api.disputes import router as disputes_router
from api.merchants import router as merchants_router
from api.stats import router as stats_router
from api.webhooks import router as webhooks_router


app = FastAPI(title="ChargeGuard AI", version="0.1.0")
app.include_router(webhooks_router)
app.include_router(disputes_router)
app.include_router(merchants_router)
app.include_router(stats_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
