from datetime import datetime, timezone

from ml.features import FEATURE_NAMES, bucket_amount, feature_vector, features_from_state


def test_features_from_state_extracts_and_clamps_signals() -> None:
    state = {
        "chargeback_id": "cb_features_001",
        "reason_code": "10.4",
        "card_network": "VISA",
        "dispute_amount": 2500.0,
        "currency": "INR",
        "filing_deadline": datetime(2026, 7, 1, tzinfo=timezone.utc),
        "merchant_profile": {},
        "transaction": {
            "otp_verified": True,
            "three_ds_authenticated": True,
            "order_history_count": 80,
            "previous_chargebacks": 2,
        },
        "shipping": {
            "status": "DELIVERED",
            "delivery_latitude": 12.9716,
            "delivery_longitude": 77.5946,
            "signature_obtained": True,
        },
        "device": {
            "fraud_score": 120,
            "vpn_detected": False,
            "geolocation_match": True,
        },
        "consortium": {
            "ethoca_match": True,
            "verifi_match": False,
            "cross_merchant_fraud_history": True,
        },
        "comms": {
            "post_delivery_interaction": True,
            "complaint_raised_before_chargeback": False,
        },
    }

    features = features_from_state(state)

    assert features["customer_order_history"] == 50
    assert features["fraud_score"] == 100.0
    assert features["delivery_confirmed"] == 1
    assert features["consortium_match"] == 1
    assert features["cross_merchant_fraud"] == 1
    assert len(feature_vector(state)) == len(FEATURE_NAMES)


def test_missing_evidence_uses_safe_defaults() -> None:
    state = {
        "chargeback_id": "cb_features_002",
        "reason_code": "unknown",
        "card_network": "VISA",
        "dispute_amount": 500.0,
        "currency": "INR",
        "filing_deadline": datetime(2026, 7, 1, tzinfo=timezone.utc),
        "merchant_profile": {},
    }

    features = features_from_state(state)

    assert features["otp_verified"] == 0
    assert features["delivery_confirmed"] == 0
    assert features["fraud_score"] == 0.0
    assert features["reason_code_encoded"] == 5


def test_amount_buckets_are_stable() -> None:
    assert bucket_amount(999.99, "INR") == 0
    assert bucket_amount(1000, "INR") == 1
    assert bucket_amount(5000, "INR") == 2
    assert bucket_amount(10000, "INR") == 3


def test_amount_buckets_use_explicit_currency_scales() -> None:
    assert bucket_amount(1000, "USD") == bucket_amount(83_000, "INR")
    assert bucket_amount(11.99, "USD") == 0
    assert bucket_amount(12.05, "USD") == 1

    usd_state = {
        "chargeback_id": "cb_features_usd",
        "reason_code": "10.4",
        "card_network": "VISA",
        "dispute_amount": 1000.0,
        "currency": "USD",
        "filing_deadline": datetime(2026, 7, 1, tzinfo=timezone.utc),
        "merchant_profile": {},
    }
    inr_state = {
        **usd_state,
        "chargeback_id": "cb_features_inr",
        "dispute_amount": 83_000.0,
        "currency": "INR",
    }

    assert (
        features_from_state(usd_state)["dispute_amount_bucket"]
        == features_from_state(inr_state)["dispute_amount_bucket"]
    )


def test_amount_buckets_reject_unknown_currency() -> None:
    try:
        bucket_amount(1000, "EUR")
    except ValueError as exc:
        assert "Unsupported dispute currency: EUR" in str(exc)
    else:
        raise AssertionError("unsupported currencies must not be bucketed silently")


def test_shipping_statuses_are_distinct_scoring_features() -> None:
    base_state = {
        "chargeback_id": "cb_shipping_status",
        "reason_code": "13.1",
        "card_network": "VISA",
        "dispute_amount": 2500.0,
        "currency": "INR",
        "filing_deadline": datetime(2026, 7, 1, tzinfo=timezone.utc),
        "merchant_profile": {},
    }

    in_transit = features_from_state(
        {**base_state, "shipping": {"status": "IN TRANSIT"}}
    )
    lost = features_from_state({**base_state, "shipping": {"status": "LOST"}})
    returned = features_from_state(
        {**base_state, "shipping": {"status": "RTO DELIVERED"}}
    )

    assert in_transit["shipping_status_encoded"] == 3
    assert lost["shipping_status_encoded"] == 0
    assert returned["shipping_status_encoded"] == 1
    assert in_transit != lost != returned
