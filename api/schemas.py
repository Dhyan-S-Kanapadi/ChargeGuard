from datetime import datetime, timedelta, timezone
import re
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

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
    store_url: str | None = Field(default=None, min_length=1, max_length=2048)
    storefront_platform: Literal["shopify", "woocommerce", "custom", "unknown"] = "unknown"
    shopify_admin_api_token: str | None = Field(default=None, min_length=1, max_length=500)
    woocommerce_api_key: str | None = Field(default=None, min_length=1, max_length=500)
    woocommerce_api_secret: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_platform_credentials(self):
        if self.shopify_admin_api_token and (self.woocommerce_api_key or self.woocommerce_api_secret):
            raise ValueError("Submit credentials for only one storefront platform at a time.")
        if bool(self.woocommerce_api_key) != bool(self.woocommerce_api_secret):
            raise ValueError("WooCommerce API key and secret must be submitted together.")
        if (self.shopify_admin_api_token or self.woocommerce_api_key) and not self.store_url:
            raise ValueError("store_url is required when platform credentials are submitted.")
        return self


class MerchantUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    vertical: Literal["ecommerce", "food_delivery", "quick_commerce"] | None = None
    store_url: str | None = Field(default=None, min_length=1, max_length=2048)
    storefront_platform: Literal["shopify", "woocommerce", "custom", "unknown"] | None = None
    shopify_admin_api_token: str | None = Field(default=None, min_length=1, max_length=500)
    woocommerce_api_key: str | None = Field(default=None, min_length=1, max_length=500)
    woocommerce_api_secret: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_platform_credentials(self):
        if self.shopify_admin_api_token and (self.woocommerce_api_key or self.woocommerce_api_secret):
            raise ValueError("Submit credentials for only one storefront platform at a time.")
        if bool(self.woocommerce_api_key) != bool(self.woocommerce_api_secret):
            raise ValueError("WooCommerce API key and secret must be submitted together.")
        return self


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
    payment_connector_id: str | None = None
    payment_connector_ids: dict[str, str] = Field(default_factory=dict)
    device_risk_connector_id: str | None = None
    razorpay_account_id: str | None = None
    shipping_provider: str | None = None
    shipping_connector_ids: dict[str, str] = Field(default_factory=dict)
    consortium_connector_ids: dict[str, str] = Field(default_factory=dict)
    support_connector_ref: str | None = None
    freshdesk_domain: str
    gmail_user_id: str | None = None
    average_order_value: float
    chargeback_history_count: int
    transaction_volume_30d_by_network: dict[str, int]
    merchant_dispute_ratio: dict[str, MerchantDisputeRatio]
    store_url: str | None = None
    storefront_platform: Literal["shopify", "woocommerce", "custom", "unknown"]
    platform_credential_verified: bool
    platform_credential_verified_at: datetime | None = None
    platform_credential_verification_reason: str | None = None


class PlatformSuggestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    store_url: str = Field(min_length=1, max_length=2048)


class PlatformSuggestionResponse(BaseModel):
    suggestion: Literal["shopify", "woocommerce", "custom", "unknown"]


class RazorpayConnectorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key_id: str = Field(min_length=8, max_length=100, pattern=r"^rzp_(test|live)_[A-Za-z0-9]+$")
    key_secret: str = Field(min_length=8, max_length=500)
    razorpay_account_id: str = Field(min_length=5, max_length=100, pattern=r"^acc_[A-Za-z0-9]+$")

    @field_validator("key_id", "key_secret", "razorpay_account_id")
    @classmethod
    def strip_connector_values(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped or any(character.isspace() for character in stripped):
            raise ValueError("Payment credential fields must not contain whitespace.")
        return stripped


class StripeConnectorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(min_length=8, max_length=500, pattern=r"^sk_(test|live)_[A-Za-z0-9_]+$")

    @field_validator("api_key")
    @classmethod
    def strip_api_key(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped or any(character.isspace() for character in stripped):
            raise ValueError("Stripe API key must not contain whitespace.")
        return stripped


class PaymentConnectorResponse(BaseModel):
    connector_id: str
    merchant_id: str
    provider: Literal["razorpay", "stripe"]
    provider_account_id: str | None = None
    status: Literal["pending", "verified", "invalid", "disconnected"]
    credential_hint: str
    verified_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    last_error_code: str | None = None


class _ShippingVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verification_tracking_id: str = Field(
        min_length=3, max_length=200, pattern=r"^[A-Za-z0-9_-]+$"
    )

    @field_validator("verification_tracking_id")
    @classmethod
    def strip_tracking_id(cls, value: str) -> str:
        return value.strip()


class ShiprocketConnectorCreate(_ShippingVerification):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=500)

    @field_validator("email", "password")
    @classmethod
    def strip_shiprocket_credentials(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped or any(character.isspace() for character in stripped):
            raise ValueError("Shiprocket credential fields must not contain whitespace.")
        return stripped


class DelhiveryConnectorCreate(_ShippingVerification):
    api_token: str = Field(min_length=8, max_length=500)

    @field_validator("api_token")
    @classmethod
    def strip_delhivery_token(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped or any(character.isspace() for character in stripped):
            raise ValueError("Delhivery API token must not contain whitespace.")
        return stripped


class ShippingConnectorVerify(_ShippingVerification):
    pass


class ShippingConnectorResponse(BaseModel):
    connector_id: str
    merchant_id: str
    provider: Literal["shiprocket", "delhivery"]
    status: Literal["pending", "verified", "invalid", "disconnected"]
    credential_hint: str
    verified_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    last_error_code: str | None = None


class GmailConnectorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    access_token: SecretStr = Field(min_length=8, max_length=8192)

    @field_validator("access_token")
    @classmethod
    def validate_token(cls, value: SecretStr) -> SecretStr:
        if any(character.isspace() or not character.isascii() or ord(character) < 33
               for character in value.get_secret_value()):
            raise ValueError("Invalid credential format.")
        return value


class FreshdeskConnectorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    api_key: SecretStr = Field(min_length=8, max_length=500)
    domain: str = Field(
        max_length=253,
        pattern=r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.freshdesk\.com$",
    )

    @field_validator("api_key")
    @classmethod
    def validate_key(cls, value: SecretStr) -> SecretStr:
        return GmailConnectorCreate.validate_token(value)


class SupportConnectorResponse(BaseModel):
    connector_id: str
    merchant_id: str
    provider: Literal["gmail", "freshdesk"]
    status: Literal["verified", "invalid", "disconnected"]
    verified_at: datetime | None
    created_at: datetime
    updated_at: datetime
    last_error_code: str | None

class _ConsortiumConnectorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    api_key: SecretStr = Field(min_length=8, max_length=500)
    base_url: str = Field(min_length=12, max_length=2048)
    verification_payment_id: str = Field(min_length=3, max_length=200)

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if any(character.isspace() or not character.isascii() or ord(character) < 33 for character in secret):
            raise ValueError("Invalid credential format.")
        return value

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.port or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                or "." not in parsed.hostname):
            raise ValueError("Provider URL must be an HTTPS origin.")
        return value.rstrip("/")

    @field_validator("verification_payment_id")
    @classmethod
    def validate_payment_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(character.isspace() for character in normalized):
            raise ValueError("Invalid verification payment identifier.")
        return normalized


class EthocaConnectorCreate(_ConsortiumConnectorCreate):
    pass


class VerifiConnectorCreate(_ConsortiumConnectorCreate):
    pass


class ConsortiumConnectorVerify(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verification_payment_id: str = Field(min_length=3, max_length=200)

    @field_validator("verification_payment_id")
    @classmethod
    def validate_payment_id(cls, value: str) -> str:
        return _ConsortiumConnectorCreate.validate_payment_id(value)


class ConsortiumConnectorResponse(BaseModel):
    connector_id: str
    merchant_id: str
    provider: Literal["ethoca", "verifi"]
    status: Literal["pending", "verified", "invalid", "disconnected"]
    verified_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    last_error_code: str | None = None


class SeonConnectorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(min_length=8, max_length=500)

    @field_validator("api_key")
    @classmethod
    def strip_api_key(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped or any(character.isspace() for character in stripped):
            raise ValueError("SEON API key must not contain whitespace.")
        return stripped


class DeviceRiskConnectorResponse(BaseModel):
    connector_id: str
    merchant_id: str
    provider: Literal["seon"]
    status: Literal["verification_pending", "verified", "invalid", "disconnected"]
    credential_hint: str
    verified_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error_code: str | None = None
    created_at: datetime
    updated_at: datetime


class OrderIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchant_id: str = Field(min_length=1, max_length=100)
    order_id: str = Field(min_length=1, max_length=200)
    customer_email: str = Field(min_length=3, max_length=320)
    customer_ip: str = Field(min_length=1, max_length=64)
    user_agent: str = Field(min_length=1, max_length=2000)
    shipping_address: str | dict[str, Any]
    order_date: datetime
    payment_provider: Literal["razorpay", "stripe"] | None = None
    provider_payment_id: str | None = Field(default=None, min_length=1, max_length=200)
    provider_order_id: str | None = Field(default=None, min_length=1, max_length=200)
    commerce_order_number: str | None = Field(default=None, min_length=1, max_length=200)
    tracking_id: str | None = Field(default=None, min_length=1, max_length=200)
    shipping_provider: Literal["shiprocket", "delhivery"] | None = None
    fulfillment_id: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator(
        "provider_payment_id",
        "provider_order_id",
        "commerce_order_number",
        "tracking_id",
        "fulfillment_id",
        mode="before",
    )
    @classmethod
    def reject_blank_optional_identifiers(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("optional order identifiers must be non-empty strings")
        return value.strip()

    @field_validator("customer_email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
            raise ValueError("customer_email must be a valid email address")
        return normalized

    @field_validator("customer_ip")
    @classmethod
    def validate_ip(cls, value: str) -> str:
        from ipaddress import ip_address

        try:
            return str(ip_address(value.strip()))
        except ValueError as exc:
            raise ValueError("customer_ip must be a valid IPv4 or IPv6 address") from exc

    @field_validator("order_date")
    @classmethod
    def normalize_order_date(cls, value: datetime) -> datetime:
        normalized = value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)
        if normalized > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("order_date must not be in the future")
        return normalized

    @field_validator("shipping_address")
    @classmethod
    def validate_shipping_address(cls, value: str | dict[str, Any]):
        if isinstance(value, str) and not value.strip():
            raise ValueError("shipping_address must not be empty")
        if isinstance(value, dict) and not value:
            raise ValueError("shipping_address must not be empty")
        return value


class OrderIngestResponse(BaseModel):
    status: Literal["created", "updated"]
    merchant_id: str
    order_id: str


class DisputeClassificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card_network: Literal["VISA", "MASTERCARD", "RUPAY", "AMEX"]
    network_reason_code: str = Field(min_length=1, max_length=20)
    actor_id: str = Field(min_length=1, max_length=200)
    suggestion_id: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("network_reason_code", "actor_id", "suggestion_id")
    @classmethod
    def strip_classification_values(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("classification values must not be blank")
        return stripped


class DisputeClassificationResponse(BaseModel):
    chargeback_id: str
    status: Literal["scheduled"]
    card_network: str
    network_reason_code: str


class ClassificationSuggestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(min_length=1, max_length=200)

    @field_validator("actor_id")
    @classmethod
    def strip_actor_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("actor_id must not be blank")
        return stripped


class ClassificationSuggestionRejectRequest(ClassificationSuggestionRequest):
    suggestion_id: str = Field(min_length=1, max_length=100)

    @field_validator("suggestion_id")
    @classmethod
    def strip_suggestion_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("suggestion_id must not be blank")
        return stripped


class ClassificationSuggestionResponse(BaseModel):
    suggestion_id: str
    card_network: str
    recommended_reason_code: str | None
    confidence: float
    rationale: str
    evidence_fields_used: list[str]
    status: Literal["pending", "approved", "rejected", "unavailable"]
    can_approve: bool
    unavailability_reason: str | None = None


class ShopifySyncResponse(BaseModel):
    merchant_id: str
    created: int
    updated: int
    failed_pages: int


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
    shipping_provider: Literal["shiprocket", "delhivery"] | None = None
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


class RazorpaySimulatorScenarioRun(BaseModel):
    merchant_id: str = Field(min_length=1, max_length=100)


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
