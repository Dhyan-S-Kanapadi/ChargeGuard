from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RazorpayCardEntity(BaseModel):
    model_config = ConfigDict(extra="allow")

    network: str | None = None


class RazorpayPaymentEntity(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    amount: int | None = None
    currency: str | None = None
    order_id: str | None = None
    method: str | None = None
    card_id: str | None = None
    card: RazorpayCardEntity | None = None
    notes: dict[str, Any] = Field(default_factory=dict)
    created_at: int | None = None


class RazorpayDisputeEntity(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    payment_id: str
    amount: int = Field(gt=0)
    currency: str
    reason_code: str
    respond_by: int | None = None
    status: str
    phase: str
    created_at: int | None = None


class RazorpayPaymentPayload(BaseModel):
    entity: RazorpayPaymentEntity


class RazorpayDisputePayload(BaseModel):
    entity: RazorpayDisputeEntity


class RazorpayWebhookPayload(BaseModel):
    payment: RazorpayPaymentPayload | None = None
    dispute: RazorpayDisputePayload


class RazorpayWebhookEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")

    entity: Literal["event"]
    account_id: str
    event: str
    payload: RazorpayWebhookPayload
    created_at: int | None = None


class RazorpayEventHeader(BaseModel):
    model_config = ConfigDict(extra="allow")

    entity: Literal["event"]
    account_id: str = ""
    event: str
    created_at: int | None = None


class NormalizedRazorpayDispute(BaseModel):
    provider: Literal["razorpay"] = "razorpay"
    provider_dispute_id: str
    chargeback_id: str
    payment_id: str
    order_id: str | None = None
    dispute_amount: Decimal
    currency: str
    filing_deadline: datetime | None = None
    deadline_overdue: bool
    provider_reason_code: str
    network_reason_code: str | None = None
    payment_rail: str | None = None
    card_network: Literal["VISA", "MASTERCARD", "RUPAY", "AMEX"] | None = None
    provider_status: str
    provider_phase: str
    provider_event: str
    provider_account_id: str
    webhook_event_id: str
    provider_event_timestamp: datetime | None = None
    enrichment_degraded: bool = False
    enrichment_failure_reason: str | None = None
