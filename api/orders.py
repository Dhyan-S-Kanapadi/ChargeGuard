from fastapi import APIRouter, Depends, HTTPException

from api.auth import require_api_key
from api.schemas import OrderIngestRequest, OrderIngestResponse
from api.store import store
from core.state import OrderRecord


router = APIRouter(
    prefix="/orders",
    tags=["orders"],
    dependencies=[Depends(require_api_key)],
)


@router.post("/ingest", response_model=OrderIngestResponse)
def ingest_order(payload: OrderIngestRequest) -> OrderIngestResponse:
    if store.get_merchant(payload.merchant_id) is None:
        raise HTTPException(status_code=404, detail="Merchant not found.")
    order: OrderRecord = {
        **payload.model_dump(),
        "is_disputed": False,
        "is_fraud_flagged": False,
    }
    created = store.upsert_order(order)
    return OrderIngestResponse(
        status="created" if created else "updated",
        merchant_id=payload.merchant_id,
        order_id=payload.order_id,
    )
