import math
import random
from typing import Any

from ml.features import FEATURE_NAMES


FeatureRow = dict[str, float | int]


def _bernoulli(rng: random.Random, probability: float) -> int:
    return int(rng.random() < probability)


def _synthetic_row(rng: random.Random) -> FeatureRow:
    consortium_match = _bernoulli(rng, 0.08)
    cross_merchant_fraud = _bernoulli(rng, 0.10 if not consortium_match else 0.55)
    delivery_confirmed = _bernoulli(rng, 0.72)
    otp_verified = _bernoulli(rng, 0.68)
    three_ds_authenticated = _bernoulli(rng, 0.65)
    customer_order_history = min(int(rng.expovariate(1 / 8)), 50)
    previous_chargebacks = min(int(rng.expovariate(1 / 0.7)), 6)
    gps_coordinates_present = delivery_confirmed * _bernoulli(rng, 0.72)
    signature_obtained = delivery_confirmed * _bernoulli(rng, 0.58)
    fraud_score = round(min(max(rng.gauss(36, 22), 0), 100), 2)
    vpn_detected = _bernoulli(rng, 0.14)
    geolocation_match = _bernoulli(rng, 0.76)
    post_delivery_contact = delivery_confirmed * _bernoulli(rng, 0.34)
    pre_chargeback_complaint = _bernoulli(rng, 0.22)
    dispute_amount_bucket = rng.choices((0, 1, 2, 3), weights=(20, 45, 25, 10))[0]
    card_network_encoded = rng.choices((0, 1, 2, 3), weights=(55, 30, 12, 3))[0]
    reason_code_encoded = rng.choices((0, 1, 2, 3, 4), weights=(30, 30, 15, 20, 5))[0]

    compelling_evidence_3_0 = int(
        otp_verified
        and three_ds_authenticated
        and customer_order_history >= 2
        and previous_chargebacks == 0
        and not consortium_match
        and not cross_merchant_fraud
    )
    shipping_status_lost = int(
        not delivery_confirmed and (vpn_detected or fraud_score >= 70)
    )
    shipping_status_returned = int(
        not delivery_confirmed
        and not shipping_status_lost
        and pre_chargeback_complaint
    )
    shipping_status_in_transit = int(
        not delivery_confirmed
        and not shipping_status_lost
        and not shipping_status_returned
    )

    return {
        "otp_verified": otp_verified,
        "three_ds_authenticated": three_ds_authenticated,
        "compelling_evidence_3_0": compelling_evidence_3_0,
        "customer_order_history": customer_order_history,
        "previous_chargebacks": previous_chargebacks,
        "delivery_confirmed": delivery_confirmed,
        "shipping_status_encoded": (
            4
            if delivery_confirmed
            else 0
            if shipping_status_lost
            else 1
            if shipping_status_returned
            else 3
            if shipping_status_in_transit
            else 2
        ),
        "gps_coordinates_present": gps_coordinates_present,
        "signature_obtained": signature_obtained,
        "fraud_score": fraud_score,
        "vpn_detected": vpn_detected,
        "geolocation_match": geolocation_match,
        "consortium_lookup_complete": 1,
        "consortium_match": consortium_match,
        "cross_merchant_fraud": cross_merchant_fraud,
        "post_delivery_contact": post_delivery_contact,
        "pre_chargeback_complaint": pre_chargeback_complaint,
        "dispute_amount_bucket": dispute_amount_bucket,
        "card_network_encoded": card_network_encoded,
        "reason_code_encoded": reason_code_encoded,
    }


def _latent_win_score(row: FeatureRow, noise: float) -> float:
    return (
        -0.65
        + 0.75 * row["otp_verified"]
        + 0.95 * row["three_ds_authenticated"]
        + 0.025 * min(row["customer_order_history"], 20)
        - 0.45 * row["previous_chargebacks"]
        + 0.85 * row["delivery_confirmed"]
        + 0.35 * row["gps_coordinates_present"]
        + 0.45 * row["signature_obtained"]
        - 0.018 * row["fraud_score"]
        - 0.70 * row["vpn_detected"]
        + 0.45 * row["geolocation_match"]
        - 2.10 * row["consortium_match"]
        - 1.25 * row["cross_merchant_fraud"]
        + 0.40 * row["post_delivery_contact"]
        - 0.75 * row["pre_chargeback_complaint"]
        + noise
    )


def generate_synthetic_dataset(
    count: int = 200,
    *,
    seed: int = 42,
) -> tuple[list[FeatureRow], list[int]]:
    """Generate deterministic development data; never treat it as production truth."""
    if count < 0:
        raise ValueError("count must be non-negative")
    if count == 0:
        return [], []
    if count == 1:
        raise ValueError("count must be 0 or at least 2")

    rng = random.Random(seed)
    rows: list[FeatureRow] = []
    labels: list[int] = []
    for _ in range(count):
        row = _synthetic_row(rng)
        label = int(_latent_win_score(row, rng.gauss(0, 0.45)) > 0)
        rows.append(row)
        labels.append(label)

    if len(set(labels)) != 2:
        raise RuntimeError("synthetic dataset must contain both outcomes")
    if any(tuple(row) != FEATURE_NAMES for row in rows):
        raise RuntimeError("synthetic feature order does not match FEATURE_NAMES")
    return rows, labels


def dataset_summary(labels: list[int]) -> dict[str, Any]:
    total = len(labels)
    wins = sum(labels)
    return {
        "records": total,
        "wins": wins,
        "losses": total - wins,
        "win_rate": wins / total if total else math.nan,
    }
