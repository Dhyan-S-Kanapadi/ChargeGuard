from datetime import datetime, timedelta, timezone

from agents.evidence.transaction import _evaluate_compelling_evidence_3_0
from ml.features import features_from_state


def _state(prior_transactions: list[dict]) -> dict:
    received_at = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    return {
        "chargeback_id": "cb_ce3_001",
        "reason_code": "10.4",
        "card_network": "VISA",
        "dispute_amount": 25_000.0,
        "currency": "INR",
        "filing_deadline": received_at + timedelta(days=30),
        "chargeback_received_at": received_at,
        "merchant_profile": {},
        "transaction": {
            "device_id": "device-123",
            "ip_address": "49.36.18.22",
            "customer_email": "buyer@example.com",
            "shipping_address": "12 Demo Road, Bengaluru",
            "prior_transactions": prior_transactions,
        },
    }


def _prior(transaction_id: str, age_days: int, **overrides) -> dict:
    transaction = {
        "transaction_id": transaction_id,
        "transaction_at": (
            datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
            - timedelta(days=age_days)
        ).isoformat(),
        "disputed": False,
        "device_id": "device-123",
        "ip_address": "49.36.18.22",
        "email": "other@example.com",
        "shipping_address": "Different address",
    }
    transaction.update(overrides)
    return transaction


def test_ce3_qualifies_with_two_prior_matching_undisputed_transactions() -> None:
    state = _state([_prior("pay_prior_1", 150), _prior("pay_prior_2", 300)])

    result = _evaluate_compelling_evidence_3_0(state)
    state["compelling_evidence_3_0"] = result

    assert result["qualifies"] is True
    assert [item["transaction_id"] for item in result["matched_transactions"]] == [
        "pay_prior_1",
        "pay_prior_2",
    ]
    assert result["matched_fields"] == ["device_id", "ip_address"]
    assert features_from_state(state)["compelling_evidence_3_0"] == 1


def test_ce3_rejects_transactions_outside_rules() -> None:
    state = _state(
        [
            _prior("too_recent", 90),
            _prior("already_disputed", 150, disputed=True),
            _prior("only_one_match", 200, ip_address="10.0.0.1"),
        ]
    )

    result = _evaluate_compelling_evidence_3_0(state)

    assert result == {
        "qualifies": False,
        "matched_transactions": [],
        "matched_fields": [],
        "reason": "insufficient_qualifying_transactions",
    }
