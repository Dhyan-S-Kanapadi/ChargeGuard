from fastapi import APIRouter, Depends, HTTPException, status

from api.auth import require_api_key
from api.schemas import MerchantCreate, MerchantResponse
from api.store import store
from core.state import MerchantProfile


router = APIRouter(
    prefix="/merchants",
    tags=["merchants"],
    dependencies=[Depends(require_api_key)],
)


def _response(profile: MerchantProfile) -> MerchantResponse:
    return MerchantResponse(
        merchant_id=profile["merchant_id"],
        name=profile["name"],
        vertical=profile["vertical"],
        payment_provider=profile.get("payment_provider"),
        shipping_provider=profile.get("shipping_provider"),
        average_order_value=profile["average_order_value"],
        chargeback_history_count=profile["chargeback_history_count"],
    )


@router.post("", response_model=MerchantResponse, status_code=status.HTTP_201_CREATED)
def create_merchant(payload: MerchantCreate) -> MerchantResponse:
    profile: MerchantProfile = payload.model_dump(exclude_none=True)  # type: ignore[assignment]
    if not store.create_merchant(profile):
        raise HTTPException(status_code=409, detail="Merchant already exists.")
    return _response(profile)


@router.get("/{merchant_id}", response_model=MerchantResponse)
def get_merchant(merchant_id: str) -> MerchantResponse:
    profile = store.get_merchant(merchant_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Merchant not found.")
    return _response(profile)
