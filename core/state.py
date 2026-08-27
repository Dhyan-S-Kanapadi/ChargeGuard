from datetime import datetime
from typing import Literal, NotRequired, Optional, TypedDict


class MerchantProfile(TypedDict):
    merchant_id: str
    name: str
    vertical: Literal["ecommerce", "food_delivery", "quick_commerce"]
    payment_provider: NotRequired[Literal["razorpay", "stripe"]]
    shipping_provider: NotRequired[Literal["shiprocket", "delhivery"]]
    # TODO: FreshdeskClient still uses FRESHDESK_DOMAIN; this is informational until it supports per-merchant domains.
    freshdesk_domain: str
    average_order_value: float
    chargeback_history_count: int


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
    order_history_count: int
    previous_chargebacks: int
    raw: dict


class ShippingEvidence(TypedDict):
    tracking_id: str
    courier: str
    status: str
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
    tracking_id: NotRequired[str]
    chargeback_received_at: NotRequired[datetime]
    card_fingerprint: NotRequired[str]
    reason_code: str
    card_network: Literal["VISA", "MASTERCARD", "RUPAY", "AMEX"]
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
    requires_human_review: NotRequired[bool]
    decision: Optional[Literal["FIGHT", "ACCEPT", "ESCALATE_DEGRADED"]]
    decision_reasoning: Optional[str]

    # Response
    rebuttal_document_path: Optional[str]
    quality_approved: bool
    quality_rejection_reason: Optional[str]
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
