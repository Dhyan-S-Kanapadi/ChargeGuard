from datetime import datetime
from typing import Any, Literal, NotRequired, Optional, TypedDict

from core.shipping_status import ShippingStatusCategory


class MerchantProfile(TypedDict):
    merchant_id: str
    name: str
    vertical: Literal["ecommerce", "food_delivery", "quick_commerce"]
    payment_provider: NotRequired[Literal["razorpay", "stripe"]]
    payment_connector_id: NotRequired[str | None]
    payment_connector_ids: NotRequired[dict[str, str]]
    device_risk_connector_id: NotRequired[str | None]
    razorpay_account_id: NotRequired[str | None]
    shipping_provider: NotRequired[Literal["shiprocket", "delhivery"]]
    support_connector_ref: NotRequired[str | None]
    freshdesk_domain: str
    gmail_user_id: NotRequired[str | None]
    average_order_value: float
    chargeback_history_count: int
    transaction_volume_30d_by_network: NotRequired[dict[str, int]]
    store_url: NotRequired[str | None]
    storefront_platform: NotRequired[Literal["shopify", "woocommerce", "custom", "unknown"]]
    shopify_admin_api_token: NotRequired[str | None]
    woocommerce_api_key: NotRequired[str | None]
    woocommerce_api_secret: NotRequired[str | None]
    platform_credential_verified: NotRequired[bool]
    platform_credential_verified_at: NotRequired[datetime | None]
    platform_credential_verification_reason: NotRequired[str | None]


class PaymentConnector(TypedDict):
    connector_id: str
    merchant_id: str
    provider: Literal["razorpay", "stripe"]
    provider_account_id: str | None
    status: Literal["pending", "verified", "invalid", "disconnected"]
    credential_hint: str
    verified_at: datetime | None
    created_at: datetime
    updated_at: datetime
    last_error_code: str | None


class DeviceRiskConnector(TypedDict):
    connector_id: str
    merchant_id: str
    provider: Literal["seon"]
    status: Literal["verification_pending", "verified", "invalid", "disconnected"]
    credential_hint: str
    verified_at: datetime | None
    last_success_at: datetime | None
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime


class OrderRecord(TypedDict):
    order_id: str
    merchant_id: str
    customer_email: str
    customer_ip: str
    user_agent: str
    shipping_address: str | dict[str, Any]
    order_date: datetime
    is_disputed: bool
    is_fraud_flagged: bool
    payment_provider: NotRequired[Literal["razorpay", "stripe"]]
    provider_payment_id: NotRequired[str]
    provider_order_id: NotRequired[str]
    commerce_order_number: NotRequired[str]
    tracking_id: NotRequired[str]
    fulfillment_id: NotRequired[str]


class CE3Qualification(TypedDict):
    qualifies: bool
    matched_elements: list[str]
    prior_transaction_refs: list[str]
    reason: str


class ClassificationSuggestion(TypedDict):
    suggestion_id: str
    card_network: Literal["VISA", "MASTERCARD", "RUPAY", "AMEX"]
    recommended_reason_code: str | None
    confidence: float
    rationale: str
    evidence_fields_used: list[str]
    model: str
    prompt_schema_version: str
    created_at: datetime
    requested_by_actor_id: str
    status: Literal["pending", "approved", "rejected", "unavailable"]
    unavailability_reason: NotRequired[str | None]
    resolved_at: NotRequired[datetime]
    resolved_by_actor_id: NotRequired[str]


class LLMDecisionReview(TypedDict):
    status: Literal["completed", "unavailable", "disabled"]
    recommendation: Literal["FIGHT", "ACCEPT", "ESCALATE_DEGRADED"] | None
    confidence: float | None
    summary: str | None
    supporting_factors: list[str]
    opposing_factors: list[str]
    missing_evidence: list[str]
    risk_flags: list[str]
    agreement_with_engine: bool | None
    model: str | None
    generated_at: datetime | None
    error_code: str | None


class TransactionEvidence(TypedDict):
    order_id: str
    payment_id: str
    amount: float
    currency: str
    otp_verified: bool
    three_ds_authenticated: bool
    device_id: str
    ip_address: str
    customer_email: str
    shipping_address: NotRequired[str | dict]
    order_history_count: int
    previous_chargebacks: int
    prior_transactions: NotRequired[list[dict]]
    provider_order_id: NotRequired[str]
    commerce_order_reference: NotRequired[str]
    commerce_order_number_reference: NotRequired[str]
    raw: dict


class ShippingEvidence(TypedDict):
    tracking_id: str
    courier: str
    status: str
    status_category: ShippingStatusCategory
    delivered_at: Optional[datetime]
    delivery_latitude: Optional[float]
    delivery_longitude: Optional[float]
    signature_obtained: bool
    delivery_photo_url: Optional[str]
    raw: dict


class CommsEvidence(TypedDict):
    emails: list[dict]
    support_tickets: list[dict]
    post_delivery_interaction: bool
    complaint_raised_before_chargeback: bool
    raw: dict


class DeviceEvidence(TypedDict):
    fraud_score: float
    device_fingerprint: str
    geolocation_match: bool
    login_pattern_normal: bool
    vpn_detected: bool
    raw: dict


class ConsortiumEvidence(TypedDict):
    lookup_complete: bool
    ethoca_match: bool
    verifi_match: bool
    cross_merchant_fraud_history: bool
    dispute_count_across_merchants: int
    raw: dict


class DeliveryPhotoEvidence(TypedDict):
    photo_url: str
    ai_verified: bool
    address_visible: bool
    timestamp_on_photo: Optional[datetime]
    raw: dict


class OrderTimelineEvidence(TypedDict):
    placed_at: datetime
    accepted_at: Optional[datetime]
    picked_at: Optional[datetime]
    delivered_at: Optional[datetime]
    post_delivery_rating: Optional[float]
    raw: dict


class ChargebackState(TypedDict):
    # Input
    chargeback_id: str
    order_id: NotRequired[str]
    payment_id: NotRequired[str]
    provider_order_id: NotRequired[str]
    commerce_order_id: NotRequired[str]
    commerce_order_number: NotRequired[str]
    tracking_id: NotRequired[str]
    fulfillment_id: NotRequired[str]
    correlation_status: NotRequired[Literal["resolved", "unresolved"]]
    correlation_source: NotRequired[str]
    correlation_failure_reason: NotRequired[str]
    chargeback_received_at: NotRequired[datetime]
    card_fingerprint: NotRequired[str]
    provider: NotRequired[str]
    provider_dispute_id: NotRequired[str]
    provider_event_id: NotRequired[str]
    webhook_event_id: NotRequired[str]
    provider_event: NotRequired[str]
    provider_event_timestamp: NotRequired[datetime]
    provider_dispute_status: NotRequired[str]
    provider_status: NotRequired[str]
    provider_phase: NotRequired[str]
    provider_reason_code: NotRequired[str]
    network_reason_code: NotRequired[str]
    reason_mapping_version: NotRequired[str]
    reason_mapping_source: NotRequired[str]
    classification_audit: NotRequired[dict[str, Any]]
    classification_suggestion: NotRequired[ClassificationSuggestion]
    classification_resume_scheduled: NotRequired[bool]
    provider_account_id: NotRequired[str]
    provider_respond_by: NotRequired[datetime]
    payment_rail: NotRequired[str]
    deadline_overdue: NotRequired[bool]
    provider_action_required: NotRequired[bool]
    reason_code: str
    card_network: Optional[Literal["VISA", "MASTERCARD", "RUPAY", "AMEX"]]
    dispute_amount: float
    currency: str
    filing_deadline: datetime
    merchant_profile: MerchantProfile

    # Orchestration
    investigation_plan: dict
    requires_food_agents: bool

    # Evidence
    transaction: Optional[TransactionEvidence]
    shipping: Optional[ShippingEvidence]
    comms: Optional[CommsEvidence]
    device: Optional[DeviceEvidence]
    consortium: Optional[ConsortiumEvidence]
    delivery_photo: Optional[DeliveryPhotoEvidence]
    order_timeline: Optional[OrderTimelineEvidence]
    evidence_collection_degraded: bool
    degraded_reasons: list[str]

    # Intelligence
    win_probability: Optional[float]
    expected_value: Optional[float]
    third_party_fraud_indicators: NotRequired[dict[str, float | str] | None]
    identity_continuity: NotRequired[dict[str, float | str] | None]
    contradiction_flags: NotRequired[list[str]]
    contradiction_summary: NotRequired[Optional[str]]
    human_review_summary: NotRequired[Optional[str]]
    compelling_evidence_3_0: NotRequired[dict]
    ce3_qualification: NotRequired[CE3Qualification]
    disputed_order_ip: NotRequired[str]
    disputed_order_user_agent: NotRequired[str]
    ce3_override_applied: NotRequired[bool]
    requires_human_review: NotRequired[bool]
    decision: Optional[Literal["FIGHT", "ACCEPT", "ESCALATE_DEGRADED"]]
    decision_reasoning: Optional[str]
    llm_decision_review: NotRequired[LLMDecisionReview]

    # Response
    rebuttal_document_path: Optional[str]
    rebuttal_build_error: NotRequired[Optional[str]]
    quality_approved: bool
    quality_rejection_reason: Optional[str]
    quality_rejection_details: NotRequired[dict]
    quality_auto_fixable: NotRequired[bool]
    quality_loop_count: int
    filing_confirmation: Optional[str]
    filed_at: Optional[datetime]

    # Learning
    final_outcome: Optional[Literal["WIN", "LOSS", "PENDING", "ACCEPTED_NO_CONTEST"]]
    outcome_reason: Optional[str]
    outcome_recorded_at: Optional[datetime]


def is_filed_dispute(state: ChargebackState) -> bool:
    return (
        state.get("decision") == "FIGHT"
        and bool(state.get("quality_approved"))
        and state.get("filed_at") is not None
        and str(state.get("filing_confirmation") or "").startswith("filed_")
    )
