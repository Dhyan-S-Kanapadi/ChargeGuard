from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from integrations.connector_config import CONNECTOR_REF_PATTERN


class MerchantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchant_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    vertical: Literal["ecommerce", "food_delivery", "quick_commerce"]
    payment_provider: Literal["razorpay", "stripe"] | None = None
    razorpay_account_id: str | None = Field(default=None, min_length=1, max_length=100)
    shipping_provider: Literal["shiprocket", "delhivery"] | None = None
    support_connector_ref: str | None = Field(
        default=None,
        pattern=f"^{CONNECTOR_REF_PATTERN}$",
    )
    freshdesk_domain: str = Field(default="", max_length=255)
    gmail_user_id: str | None = Field(default=None, min_length=1, max_length=320)
    average_order_value: float = Field(default=0, ge=0)
    chargeback_history_count: int = Field(default=0, ge=0)
    transaction_volume_30d_by_network: dict[
        Literal["VISA", "MASTERCARD", "RUPAY", "AMEX"],
        Annotated[int, Field(ge=0)],
    ] = Field(default_factory=dict)


class MerchantDisputeRatio(BaseModel):
    window_days: int
    card_network: str
    dispute_count: int
    transaction_count: int | None
    current_ratio_pct: float | None
    threshold_pct: float | None
    status: Literal["OK", "WARNING", "UNAVAILABLE", "UNCONFIGURED"]


class MerchantResponse(BaseModel):
    merchant_id: str
    name: str
    vertical: str
    payment_provider: str | None = None
    razorpay_account_id: str | None = None
    shipping_provider: str | None = None
    support_connector_ref: str | None = None
    freshdesk_domain: str
    gmail_user_id: str | None = None
    average_order_value: float
    chargeback_history_count: int
    transaction_volume_30d_by_network: dict[str, int]
    merchant_dispute_ratio: dict[str, MerchantDisputeRatio]


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
    simulate_evidence_degraded: bool = False

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
    human_review_summary: str | None = None
    merchant_dispute_ratio: MerchantDisputeRatio | None = None
    error: str | None
    created_at: datetime
    updated_at: datetime


class RazorpaySimulatorCreate(BaseModel):
    merchant_id: str = Field(min_length=1, max_length=100)
    payment_id: str = Field(min_length=1, max_length=200)
    order_id: str = Field(min_length=1, max_length=200)
    payment_amount_paise: int = Field(gt=0)
    dispute_amount_paise: int = Field(gt=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    method: Literal["card", "upi", "netbanking", "wallet"] = "card"
    card_network: Literal["VISA", "MASTERCARD", "RUPAY", "AMEX"] | None = None
    network_reason_code: str | None = Field(default=None, min_length=1, max_length=20)
    razorpay_reason_code: str = Field(min_length=1, max_length=100)
    customer_email: str | None = Field(default=None, max_length=320)
    customer_contact: str | None = Field(default=None, max_length=50)
    vpa: str | None = Field(default=None, max_length=255)
    respond_within_hours: int = Field(default=72, ge=-(24 * 90), le=24 * 90)

    @field_validator("currency")
    @classmethod
    def normalize_simulator_currency(cls, value: str) -> str:
        return value.upper()


class RazorpaySimulatorTransition(BaseModel):
    state: Literal["action_required", "under_review", "won", "lost", "closed"]
    force: bool = False


class RazorpayReconciliationRequest(BaseModel):
    merchant_id: str = Field(min_length=1, max_length=100)
    from_timestamp: int | None = Field(default=None, ge=0)
    to_timestamp: int | None = Field(default=None, ge=0)
    count: int = Field(default=100, ge=1, le=100)
    skip: int = Field(default=0, ge=0)


class CaseSummaryResponse(BaseModel):
    chargeback_id: str
    human_review_summary: str


class AssistantQuery(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    chargeback_id: str | None = Field(default=None, min_length=1, max_length=100)


class AssistantResponse(BaseModel):
    answer: str
    based_on: dict[str, int | bool]


class OutcomeUpdate(BaseModel):
    outcome: Literal["WIN", "LOSS"]
    reason: str | None = Field(default=None, max_length=2000)


class OutcomeResponse(BaseModel):
    chargeback_id: str
    final_outcome: Literal["WIN", "LOSS"]
    outcome_reason: str
    outcome_recorded_at: datetime
