from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class MerchantCreate(BaseModel):
    merchant_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    vertical: Literal["ecommerce", "food_delivery", "quick_commerce"]
    payment_provider: Literal["razorpay", "stripe"] | None = None
    shipping_provider: Literal["shiprocket", "delhivery"] | None = None
    freshdesk_domain: str = ""
    average_order_value: float = Field(default=0, ge=0)
    chargeback_history_count: int = Field(default=0, ge=0)


class MerchantResponse(BaseModel):
    merchant_id: str
    name: str
    vertical: str
    payment_provider: str | None = None
    shipping_provider: str | None = None
    average_order_value: float
    chargeback_history_count: int


class ChargebackWebhookPayload(BaseModel):
    chargeback_id: str = Field(min_length=1, max_length=100)
    reason_code: str = Field(min_length=1, max_length=20)
    card_network: Literal["VISA", "MASTERCARD", "RUPAY", "AMEX"]
    dispute_amount: float = Field(gt=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    filing_deadline: datetime
    merchant_id: str = Field(min_length=1, max_length=100)
    order_id: str = Field(min_length=1, max_length=200)
    payment_id: str = Field(min_length=1, max_length=200)
    tracking_id: str | None = None
    card_fingerprint: str | None = None

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()

    @field_validator("filing_deadline")
    @classmethod
    def require_future_deadline(cls, value: datetime) -> datetime:
        normalized = value.replace(tzinfo=value.tzinfo or timezone.utc)
        if normalized <= datetime.now(timezone.utc):
            raise ValueError("filing_deadline must be in the future")
        return normalized


class WebhookAccepted(BaseModel):
    status: Literal["received"]
    chargeback_id: str


class DisputeSummary(BaseModel):
    chargeback_id: str
    status: str
    decision: str | None
    dispute_amount: float
    currency: str
    merchant_id: str
    created_at: datetime
    updated_at: datetime


class DisputeDetail(BaseModel):
    chargeback_id: str
    status: str
    state: dict[str, Any]
    win_probability: float | None = None
    expected_value: float | None = None
    third_party_fraud_indicators: dict[str, float | str] | None = None
    identity_continuity: dict[str, float | str] | None = None
    error: str | None
    created_at: datetime
    updated_at: datetime


class OutcomeUpdate(BaseModel):
    outcome: Literal["WIN", "LOSS"]
    reason: str | None = Field(default=None, max_length=2000)


class OutcomeResponse(BaseModel):
    chargeback_id: str
    final_outcome: Literal["WIN", "LOSS"]
    outcome_reason: str
    outcome_recorded_at: datetime
