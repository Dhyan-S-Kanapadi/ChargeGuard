from typing import Final

from core.state import ChargebackState


FEATURE_NAMES: Final[tuple[str, ...]] = (
    "otp_verified",
    "three_ds_authenticated",
    "customer_order_history",
    "previous_chargebacks",
    "delivery_confirmed",
    "gps_coordinates_present",
    "signature_obtained",
    "fraud_score",
    "vpn_detected",
    "geolocation_match",
    "consortium_lookup_complete",
    "consortium_match",
    "cross_merchant_fraud",
    "post_delivery_contact",
    "pre_chargeback_complaint",
    "dispute_amount_bucket",
    "card_network_encoded",
    "reason_code_encoded",
)

_NETWORK_ENCODINGS: Final = {
    "VISA": 0,
    "MASTERCARD": 1,
    "RUPAY": 2,
    "AMEX": 3,
}

_REASON_CODE_ENCODINGS: Final = {
    "10.4": 0,
    "13.1": 1,
    "13.3": 2,
    "4853": 3,
    "UA02": 4,
}


def bucket_amount(amount: float) -> int:
    """Bucket dispute value using merchant-friendly INR ranges."""
    if amount < 1_000:
        return 0
    if amount < 5_000:
        return 1
    if amount < 10_000:
        return 2
    return 3


def encode_network(network: str) -> int:
    return _NETWORK_ENCODINGS.get(network.upper(), len(_NETWORK_ENCODINGS))


def encode_reason_code(reason_code: str) -> int:
    return _REASON_CODE_ENCODINGS.get(reason_code.upper(), len(_REASON_CODE_ENCODINGS))


def features_from_state(state: ChargebackState) -> dict[str, float | int]:
    """Create the deterministic model feature vector from chargeback evidence."""
    transaction = state.get("transaction") or {}
    shipping = state.get("shipping") or {}
    device = state.get("device") or {}
    consortium = state.get("consortium") or {}
    comms = state.get("comms") or {}

    features: dict[str, float | int] = {
        "otp_verified": int(bool(transaction.get("otp_verified"))),
        "three_ds_authenticated": int(bool(transaction.get("three_ds_authenticated"))),
        "customer_order_history": min(max(int(transaction.get("order_history_count", 0)), 0), 50),
        "previous_chargebacks": max(int(transaction.get("previous_chargebacks", 0)), 0),
        "delivery_confirmed": int(str(shipping.get("status", "")).upper() == "DELIVERED"),
        "gps_coordinates_present": int(
            shipping.get("delivery_latitude") is not None
            and shipping.get("delivery_longitude") is not None
        ),
        "signature_obtained": int(bool(shipping.get("signature_obtained"))),
        "fraud_score": min(max(float(device.get("fraud_score", 0.0)), 0.0), 100.0),
        "vpn_detected": int(bool(device.get("vpn_detected"))),
        "geolocation_match": int(bool(device.get("geolocation_match"))),
        "consortium_lookup_complete": int(bool(consortium.get("lookup_complete"))),
        "consortium_match": int(
            bool(consortium.get("ethoca_match")) or bool(consortium.get("verifi_match"))
        ),
        "cross_merchant_fraud": int(bool(consortium.get("cross_merchant_fraud_history"))),
        "post_delivery_contact": int(bool(comms.get("post_delivery_interaction"))),
        "pre_chargeback_complaint": int(bool(comms.get("complaint_raised_before_chargeback"))),
        "dispute_amount_bucket": bucket_amount(float(state["dispute_amount"])),
        "card_network_encoded": encode_network(state["card_network"]),
        "reason_code_encoded": encode_reason_code(state["reason_code"]),
    }
    return features


def feature_vector(state: ChargebackState) -> list[float]:
    features = features_from_state(state)
    return [float(features[name]) for name in FEATURE_NAMES]
