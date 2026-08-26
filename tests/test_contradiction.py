from datetime import datetime, timezone

from agents.contradiction import contradictions_from_state


def _state(reason_code: str) -> dict:
    return {
        "chargeback_id": "cb_contradiction_001",
        "reason_code": reason_code,
        "card_network": "VISA",
        "dispute_amount": 2_500.0,
        "currency": "INR",
        "filing_deadline": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "merchant_profile": {},
        "transaction": {
            "otp_verified": True,
            "three_ds_authenticated": True,
        },
        "shipping": {
            "status": "DELIVERED",
            "signature_obtained": True,
        },
        "comms": {
            "post_delivery_interaction": True,
            "complaint_raised_before_chargeback": False,
        },
    }


def test_unauthorized_claim_flags_authenticated_strong_case() -> None:
    result = contradictions_from_state(_state("10.4"))

    assert result["flags"] == [
        "claims unauthorized, but payment was OTP-authenticated and customer engaged with support after delivery",
        "claims unauthorized, but 3DS-authenticated with no prior complaint filed",
    ]
    assert result["summary"] == (
        "2 evidence contradiction(s) identified: claims unauthorized, but payment was "
        "OTP-authenticated and customer engaged with support after delivery; claims "
        "unauthorized, but 3DS-authenticated with no prior complaint filed"
    )


def test_non_receipt_claim_flags_signed_delivery() -> None:
    result = contradictions_from_state(_state("13.1"))

    assert result["flags"] == [
        "claims non-receipt, but delivery confirmed with signature on file",
    ]
