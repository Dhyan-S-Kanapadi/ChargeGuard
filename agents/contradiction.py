from collections.abc import Callable
from typing import Any

from core.state import ChargebackState


UNAUTHORIZED_REASON_CODES = frozenset({"10.4"})
NOT_RECEIVED_REASON_CODES = frozenset({"13.1"})


def _otp_and_post_delivery_contact(state: ChargebackState) -> bool:
    transaction = state.get("transaction") or {}
    comms = state.get("comms") or {}
    return bool(transaction.get("otp_verified")) and bool(comms.get("post_delivery_interaction"))


def _three_ds_without_prior_complaint(state: ChargebackState) -> bool:
    transaction = state.get("transaction") or {}
    comms = state.get("comms") or {}
    return bool(transaction.get("three_ds_authenticated")) and not bool(
        comms.get("complaint_raised_before_chargeback")
    )


def _confirmed_delivery_with_signature(state: ChargebackState) -> bool:
    shipping = state.get("shipping") or {}
    return str(shipping.get("status", "")).upper() == "DELIVERED" and bool(
        shipping.get("signature_obtained")
    )


Rule = tuple[frozenset[str], Callable[[ChargebackState], bool], str]
CONTRADICTION_RULES: tuple[Rule, ...] = (
    (
        UNAUTHORIZED_REASON_CODES,
        _otp_and_post_delivery_contact,
        "claims unauthorized, but payment was OTP-authenticated and customer engaged with support after delivery",
    ),
    (
        UNAUTHORIZED_REASON_CODES,
        _three_ds_without_prior_complaint,
        "claims unauthorized, but 3DS-authenticated with no prior complaint filed",
    ),
    (
        NOT_RECEIVED_REASON_CODES,
        _confirmed_delivery_with_signature,
        "claims non-receipt, but delivery confirmed with signature on file",
    ),
)


def contradictions_from_state(state: ChargebackState) -> dict[str, Any]:
    """Evaluate the reason-code-specific evidence contradiction rules."""
    reason_code = state["reason_code"].upper()
    flags = [
        flag
        for reason_codes, predicate, flag in CONTRADICTION_RULES
        if reason_code in reason_codes and predicate(state)
    ]
    summary = (
        f"{len(flags)} evidence contradiction(s) identified: {'; '.join(flags)}"
        if flags
        else None
    )
    return {"flags": flags, "summary": summary}
